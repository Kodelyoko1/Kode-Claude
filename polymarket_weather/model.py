"""
XGBoost forecasting model for weather event probabilities.

The model takes daily weather features and outputs P(event=YES) for a
specific weather threshold question (e.g. "max temp > 90°F on July 15").

Training data is built by aligning historical Open-Meteo data with resolved
PolyMarket outcomes. We also generate synthetic training data from weather
climatology when real market resolution data is sparse.

Metrics tracked: accuracy, Brier score, AUC-ROC, log-loss, calibration curve.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

ROOT       = Path(__file__).parent.parent
MODELS_DIR = ROOT / "data" / "pw_models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

FEATURE_COLS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
    # engineered
    "temp_range",        # max - min
    "temp_anomaly_7d",   # vs 7-day rolling avg
    "precip_7d_sum",     # rolling 7-day precip
    "month_sin",
    "month_cos",
    "doy_sin",           # day of year seasonality
    "doy_cos",
]


def engineer_features(records: list[dict]) -> list[dict]:
    """
    Add derived features to a list of daily weather dicts (sorted by date).
    Returns new list with engineered columns appended in-place.
    """
    result = []
    temps_max = [r.get("temperature_2m_max") for r in records]
    precips    = [r.get("precipitation_sum") or 0.0 for r in records]

    for i, rec in enumerate(records):
        row = dict(rec)

        tmax = row.get("temperature_2m_max") or 0.0
        tmin = row.get("temperature_2m_min") or 0.0
        row["temp_range"] = tmax - tmin

        # 7-day rolling average temperature anomaly
        window_max = [v for v in temps_max[max(0, i-7):i] if v is not None]
        avg7 = sum(window_max) / len(window_max) if window_max else tmax
        row["temp_anomaly_7d"] = tmax - avg7

        # 7-day cumulative precip
        row["precip_7d_sum"] = sum(precips[max(0, i-7):i+1])

        # Seasonal Fourier features
        try:
            dt   = datetime.strptime(row["date"], "%Y-%m-%d")
            month = dt.month
            doy   = dt.timetuple().tm_yday
            row["month_sin"] = math.sin(2 * math.pi * month / 12)
            row["month_cos"] = math.cos(2 * math.pi * month / 12)
            row["doy_sin"]   = math.sin(2 * math.pi * doy / 365)
            row["doy_cos"]   = math.cos(2 * math.pi * doy / 365)
        except (ValueError, KeyError):
            row["month_sin"] = row["month_cos"] = row["doy_sin"] = row["doy_cos"] = 0.0

        result.append(row)
    return result


def records_to_matrix(records: list[dict]) -> tuple[list[list[float]], list[float | None]]:
    """
    Convert records → (X, y) where X is a 2-D list and y is the outcome col.
    Missing values are filled with 0.0.
    """
    X, y = [], []
    for r in records:
        row = [float(r.get(col) or 0.0) for col in FEATURE_COLS]
        X.append(row)
        y.append(r.get("outcome"))
    return X, y


# ---------------------------------------------------------------------------
# Synthetic label generation (for pre-training when market outcomes are sparse)
# ---------------------------------------------------------------------------

def generate_synthetic_labels(
    records: list[dict],
    event_type: str = "temp_above_90f",
    threshold: float | None = None,
) -> list[int]:
    """
    Create binary labels from raw weather data so the model can be pre-trained
    even without resolved PolyMarket outcomes.

    Supported event_types:
      temp_above_90f   — daily max > 32.2°C (90°F)
      temp_above_32f   — daily max > 0°C (freeze threshold)
      precip_any       — any precipitation (> 0.5mm)
      precip_1in       — heavy rain (> 25.4mm)
      wind_above_25mph — max wind > 40.2 km/h
    """
    F_TO_C = lambda f: (f - 32) / 1.8
    PRESETS = {
        "temp_above_90f":   ("temperature_2m_max", F_TO_C(90)),
        "temp_above_32f":   ("temperature_2m_max", 0.0),
        "precip_any":       ("precipitation_sum",  0.5),
        "precip_1in":       ("precipitation_sum",  25.4),
        "wind_above_25mph": ("wind_speed_10m_max", 40.2),
    }
    if event_type in PRESETS:
        col, thresh = PRESETS[event_type]
    else:
        col, thresh = "temperature_2m_max", threshold or F_TO_C(90)

    return [1 if float(r.get(col) or 0.0) >= thresh else 0 for r in records]


# ---------------------------------------------------------------------------
# Model wrapper — XGBoost with numpy fallback
# ---------------------------------------------------------------------------

class WeatherForecastModel:
    """
    Probability estimator for binary weather events.

    Uses XGBoost when available; falls back to a simple logistic regression
    implemented with numpy so the agent runs even on minimal environments.
    """

    def __init__(self, event_type: str = "temp_above_90f"):
        self.event_type = event_type
        self._xgb_model = None
        self._lr_weights: Optional[np.ndarray] = None
        self._lr_bias: float = 0.0
        self._feature_cols = FEATURE_COLS
        self._trained = False
        self._metrics: dict = {}

    # -----------------------------------------------------------------------
    # Training
    # -----------------------------------------------------------------------

    def fit(self, X: list[list[float]], y: list[int], eval_fraction: float = 0.2) -> dict:
        """
        Train on (X, y). Returns evaluation metrics on held-out eval set.
        """
        Xarr = np.array(X, dtype=np.float32)
        yarr = np.array(y, dtype=np.int32)

        n      = len(yarr)
        n_eval = max(1, int(n * eval_fraction))
        Xtr, Xev = Xarr[:-n_eval], Xarr[-n_eval:]
        ytr, yev = yarr[:-n_eval], yarr[-n_eval:]

        try:
            import xgboost as xgb
            dtrain = xgb.DMatrix(Xtr, label=ytr)
            deval  = xgb.DMatrix(Xev, label=yev)
            params = {
                "objective":        "binary:logistic",
                "eval_metric":      ["logloss", "auc"],
                "eta":              0.05,
                "max_depth":        4,
                "min_child_weight": 3,
                "subsample":        0.8,
                "colsample_bytree": 0.8,
                "seed":             42,
            }
            evals_result: dict = {}
            self._xgb_model = xgb.train(
                params,
                dtrain,
                num_boost_round=300,
                evals=[(deval, "eval")],
                evals_result=evals_result,
                early_stopping_rounds=30,
                verbose_eval=False,
            )
            preds = self._xgb_model.predict(deval)
        except ImportError:
            preds = self._fit_logistic(Xtr, ytr, Xev)

        self._trained = True
        self._metrics = _evaluate(yev, preds)
        return self._metrics

    def _fit_logistic(
        self,
        Xtr: "np.ndarray",
        ytr: "np.ndarray",
        Xev: "np.ndarray",
    ) -> "np.ndarray":
        """Minimal SGD logistic regression fallback (no sklearn dependency)."""
        n_feat = Xtr.shape[1]
        W = np.zeros(n_feat, dtype=np.float32)
        b = np.float32(0.0)
        lr = 0.01
        for epoch in range(200):
            logits = Xtr @ W + b
            probs  = 1 / (1 + np.exp(-np.clip(logits, -10, 10)))
            err    = probs - ytr
            W -= lr * Xtr.T @ err / len(ytr)
            b -= lr * err.mean()
            lr *= 0.999
        self._lr_weights = W
        self._lr_bias    = float(b)
        logits_ev = Xev @ W + b
        return 1 / (1 + np.exp(-np.clip(logits_ev, -10, 10)))

    # -----------------------------------------------------------------------
    # Inference
    # -----------------------------------------------------------------------

    def predict_proba(self, records: list[dict]) -> list[float]:
        """
        Return P(event=YES) for each record in `records`.
        Records should already have engineered features (call engineer_features first).
        """
        if not self._trained:
            return [0.5] * len(records)

        X, _ = records_to_matrix(records)
        Xarr = np.array(X, dtype=np.float32)

        if self._xgb_model is not None:
            import xgboost as xgb
            dm = xgb.DMatrix(Xarr)
            preds = self._xgb_model.predict(dm)
        elif self._lr_weights is not None:
            logits = Xarr @ self._lr_weights + self._lr_bias
            preds  = 1 / (1 + np.exp(-np.clip(logits, -10, 10)))
        else:
            return [0.5] * len(records)

        return [float(p) for p in preds]

    def predict_single(self, record: dict) -> float:
        return self.predict_proba([record])[0]

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def save(self, name: str | None = None) -> Path:
        name = name or self.event_type
        path = MODELS_DIR / f"{name}.json"
        payload: dict = {
            "event_type":    self.event_type,
            "feature_cols":  self._feature_cols,
            "trained":       self._trained,
            "metrics":       self._metrics,
            "lr_weights":    (self._lr_weights.tolist()
                              if self._lr_weights is not None else None),
            "lr_bias":       self._lr_bias,
        }
        if self._xgb_model is not None:
            xgb_path = MODELS_DIR / f"{name}_xgb.ubj"
            self._xgb_model.save_model(str(xgb_path))
            payload["xgb_model_path"] = str(xgb_path)
        path.write_text(json.dumps(payload, indent=2))
        return path

    @classmethod
    def load(cls, name: str) -> "WeatherForecastModel":
        path = MODELS_DIR / f"{name}.json"
        if not path.exists():
            return cls(event_type=name)
        data = json.loads(path.read_text())
        m = cls(event_type=data["event_type"])
        m._feature_cols = data.get("feature_cols", FEATURE_COLS)
        m._trained       = data.get("trained", False)
        m._metrics       = data.get("metrics", {})
        m._lr_bias       = data.get("lr_bias", 0.0)
        if data.get("lr_weights"):
            m._lr_weights = np.array(data["lr_weights"], dtype=np.float32)
        if data.get("xgb_model_path"):
            try:
                import xgboost as xgb
                booster = xgb.Booster()
                booster.load_model(data["xgb_model_path"])
                m._xgb_model = booster
            except (ImportError, Exception):
                pass
        return m


# ---------------------------------------------------------------------------
# Training pipeline
# ---------------------------------------------------------------------------

def train_all_models(
    weather_records_by_city: dict[str, list[dict]],
    progress_cb=None,
) -> dict[str, dict]:
    """
    Train one model per event type using historical weather data.
    Returns {event_type: metrics} mapping.
    """
    EVENT_TYPES = [
        "temp_above_90f",
        "temp_above_32f",
        "precip_any",
        "precip_1in",
        "wind_above_25mph",
    ]
    all_records: list[dict] = []
    for city, records in weather_records_by_city.items():
        engineered = engineer_features(records)
        all_records.extend(engineered)

    all_records.sort(key=lambda r: r.get("date", ""))
    results = {}

    for event_type in EVENT_TYPES:
        if progress_cb:
            progress_cb(f"Training model: {event_type}…")

        labels  = generate_synthetic_labels(all_records, event_type)
        labeled = [dict(r, outcome=lbl)
                   for r, lbl in zip(all_records, labels)]
        labeled = [r for r in labeled if r["outcome"] is not None]

        if len(labeled) < 50:
            results[event_type] = {"error": "insufficient data", "n": len(labeled)}
            continue

        X, y = records_to_matrix(labeled)
        model = WeatherForecastModel(event_type=event_type)
        metrics = model.fit(X, [int(v) for v in y])
        model.save(event_type)
        results[event_type] = metrics

    return results


# ---------------------------------------------------------------------------
# Evaluation utilities
# ---------------------------------------------------------------------------

def _evaluate(y_true: "np.ndarray", y_pred: "np.ndarray") -> dict:
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)

    # Brier score
    brier = float(np.mean((y_pred - y_true) ** 2))

    # Binary accuracy at 0.5 threshold
    preds_bin = (y_pred >= 0.5).astype(int)
    acc = float(np.mean(preds_bin == y_true))

    # Precision, recall
    tp = int(np.sum((preds_bin == 1) & (y_true == 1)))
    fp = int(np.sum((preds_bin == 1) & (y_true == 0)))
    fn = int(np.sum((preds_bin == 0) & (y_true == 1)))
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # Log-loss
    eps = 1e-7
    logloss = -float(np.mean(
        y_true * np.log(y_pred + eps) + (1 - y_true) * np.log(1 - y_pred + eps)
    ))

    # Baseline Brier (naive prior)
    baseline_prob = float(y_true.mean())
    baseline_brier = float(np.mean((baseline_prob - y_true) ** 2))
    brier_skill = 1.0 - brier / baseline_brier if baseline_brier > 0 else 0.0

    return {
        "n":              len(y_true),
        "accuracy":       round(acc, 4),
        "precision":      round(precision, 4),
        "recall":         round(recall, 4),
        "brier_score":    round(brier, 4),
        "brier_skill":    round(brier_skill, 4),  # > 0 means better than naive
        "log_loss":       round(logloss, 4),
        "event_rate":     round(baseline_prob, 4),
    }
