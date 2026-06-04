"""
PantryChef — personalized weekly meal plans from user pantry.
Revenue: $14/mo basic, $29/mo full + family, $79 one-time 30-day deep package.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autonomous import storage, mailer, billing, metrics
from pantrychef import health

AGENT_KEY = "pantrychef"
USERS_DIR = Path(__file__).parent.parent / "data" / "pc_users"
PLANS_DIR = Path(__file__).parent.parent / "data" / "pc_plans"


RECIPE_RULES = [
    # (required_ingredients_subset, recipe_template)
    ({"rice", "egg"},
     {"name": "Quick Egg Fried Rice", "prep": 15,
      "steps": ["Cook rice (or use leftover)", "Whisk eggs, scramble in oil",
                "Add rice; season with soy or salt", "Top with scallion if available"]}),
    ({"pasta", "tomato"},
     {"name": "Pantry Tomato Pasta", "prep": 20,
      "steps": ["Boil pasta until al dente", "Reduce tomato with garlic + olive oil",
                "Toss pasta in sauce; finish with cheese or olive oil"]}),
    ({"chicken", "rice"},
     {"name": "One-Pan Chicken & Rice", "prep": 40,
      "steps": ["Sear chicken in pan", "Remove; sauté onion + garlic",
                "Add rice + 2x water; nestle chicken back in; cover 25min"]}),
    ({"beans", "rice"},
     {"name": "Beans & Rice Bowl", "prep": 25,
      "steps": ["Cook rice", "Simmer beans with cumin + onion",
                "Plate over rice; add hot sauce or lime"]}),
    ({"oats", "milk"},
     {"name": "Overnight Oats", "prep": 5,
      "steps": ["Mix oats + milk 1:1 in jar", "Refrigerate overnight",
                "Top with fruit or honey"]}),
    ({"bread", "egg"},
     {"name": "Egg-in-a-Hole", "prep": 8,
      "steps": ["Cut hole in bread slice", "Butter pan",
                "Crack egg into hole; cook each side 90s"]}),
    ({"potato"},
     {"name": "Roasted Potatoes", "prep": 35,
      "steps": ["Cube potatoes", "Toss with oil + salt", "Roast 425°F for 30min"]}),
    ({"lentil"},
     {"name": "Lentil Soup", "prep": 30,
      "steps": ["Sauté onion + carrot", "Add lentils + 4 cups water/broth",
                "Simmer 25min; season"]}),
    ({"tortilla", "bean"},
     {"name": "Bean Quesadilla", "prep": 10,
      "steps": ["Mash beans into tortilla", "Add cheese", "Crisp in dry pan, flip once"]}),
    ({"pasta", "garlic"},
     {"name": "Aglio e Olio", "prep": 12,
      "steps": ["Boil pasta", "Slow-cook garlic in olive oil with chili flake",
                "Toss with pasta + parsley"]}),
]


def normalize_pantry(pantry: dict) -> set:
    items = set()
    for category, ingredients in pantry.items():
        if isinstance(ingredients, list):
            for i in ingredients:
                if isinstance(i, dict):
                    items.add(i.get("name", "").lower())
                else:
                    items.add(str(i).lower())
        elif isinstance(ingredients, str):
            items.add(ingredients.lower())
    # crude pluralization handling
    bases = set()
    for i in items:
        bases.add(i.rstrip("s"))
    return items | bases


def select_recipes(pantry_set: set, allergies: set, dislikes: set, count: int = 21) -> list:
    candidates = []
    for required, recipe in RECIPE_RULES:
        if not required.issubset(pantry_set):
            # Check if at most 1 missing — then add to shopping
            missing = required - pantry_set
            if len(missing) > 1:
                continue
            recipe = dict(recipe)
            recipe["needs"] = list(missing)
        else:
            recipe = dict(recipe)
            recipe["needs"] = []
        if any(a in recipe["name"].lower() for a in allergies):
            continue
        if any(d in recipe["name"].lower() for d in dislikes):
            continue
        candidates.append(recipe)
    # cycle to fill count
    selected = []
    while len(selected) < count and candidates:
        for r in candidates:
            selected.append(r)
            if len(selected) >= count:
                break
    return selected[:count]


def build_plan(user_id: str) -> dict:
    user_file = USERS_DIR / f"{user_id}.json"
    if not user_file.exists():
        return {"error": "no_user_profile"}
    import json
    profile = json.loads(user_file.read_text())
    pantry = profile.get("pantry", {})
    prefs = profile.get("preferences", {})
    allergies = {a.lower() for a in prefs.get("allergies", [])}
    dislikes = {d.lower() for d in prefs.get("dislikes", [])}
    pantry_set = normalize_pantry(pantry)
    if len(pantry_set) < 5:
        return {"error": "pantry_too_small", "items": len(pantry_set)}

    recipes = select_recipes(pantry_set, allergies, dislikes, count=21)

    # Build plan
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    plan_lines = [f"# Weekly Meal Plan — {profile.get('name', user_id)}",
                  f"_{datetime.now():%B %d, %Y}_\n"]
    i = 0
    shopping_set = set()
    for d in days:
        plan_lines.append(f"## {d}")
        for meal in ("Breakfast", "Lunch", "Dinner"):
            if i < len(recipes):
                r = recipes[i]
                plan_lines.append(f"- **{meal}:** {r['name']} ({r['prep']} min)")
                shopping_set.update(r.get("needs", []))
                i += 1
        plan_lines.append("")

    # Shopping list (consolidated)
    shopping_lines = ["# Shopping List\n"]
    for item in sorted(shopping_set):
        shopping_lines.append(f"- [ ] {item}")
    if not shopping_set:
        shopping_lines.append("_All ingredients in pantry — nothing to buy!_")

    # Recipes detail
    recipes_lines = ["# Recipes This Week\n"]
    seen = set()
    for r in recipes:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        recipes_lines.append(f"## {r['name']} ({r['prep']} min)")
        for step in r["steps"]:
            recipes_lines.append(f"1. {step}")
        recipes_lines.append("")

    out_dir = PLANS_DIR / user_id / datetime.now().strftime("%Y-W%W")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.md").write_text("\n".join(plan_lines))
    (out_dir / "shopping_list.md").write_text("\n".join(shopping_lines))
    (out_dir / "recipes.md").write_text("\n".join(recipes_lines))

    return {"plan_dir": str(out_dir), "recipes_count": len(recipes),
            "shopping_items": len(shopping_set)}


def fulfill_cycle() -> dict:
    subs = storage.load("pc_subscribers.json", [])
    sent = 0
    for s in subs:
        if s.get("status") != "active":
            continue
        user_id = s.get("user_id", "")
        result = build_plan(user_id)
        if "error" in result:
            err = result["error"]
            detail = ""
            if err == "pantry_too_small":
                detail = f"items={result.get('items', 0)}"
            health.record_plan(user_id, err, detail=detail)
            continue
        recipient = s.get("email", "")
        if not recipient:
            health.record_plan(user_id, "no_email")
            continue
        plan_dir = Path(result["plan_dir"])
        attachments = [str(p) for p in plan_dir.glob("*.md")]
        body = (
            f"Hi {s.get('name', 'there')},\n\n"
            f"Your meal plan for the week is attached:\n"
            f"- {result['recipes_count']} meals planned\n"
            f"- Shopping list: {result['shopping_items']} items\n\n"
            f"Reply if you want to swap any meals.\n\n"
            f"— PantryChef"
        )
        r = mailer.send(AGENT_KEY, recipient,
                        f"PantryChef — week of {datetime.now():%b %d}",
                        body, purpose="fulfillment", attachments=attachments)
        if r.get("status") == "sent":
            sent += 1
            health.record_plan(user_id, "success",
                               detail=f"recipes={result['recipes_count']} shopping={result['shopping_items']}")
            health.record_yield(user_id, result["recipes_count"], result["shopping_items"])
        else:
            health.record_plan(user_id, "mail_failed",
                               detail=f"mailer={r.get('status','?')}: "
                                      f"{(r.get('reason') or r.get('error',''))[:80]}")
    return {"fulfillment_sent": sent}


def acquire_cycle() -> dict:
    leads = storage.load("pc_leads.json", [])
    sent = 0
    for lead in leads:
        if lead.get("trial_sent"):
            continue
        body = (
            f"Hi,\n\nFree pantry-flip sample: paste 5 pantry items and reply, "
            f"and we'll send 3 recipes you can make right now.\n\n"
            f"Want the full weekly meal plan + shopping list? $14/mo → paypal.me/wholesaleomniverse/14\n"
            f"30-day deep plan: $79 → paypal.me/wholesaleomniverse/79"
        )
        result = mailer.send(AGENT_KEY, lead["email"],
                             "Free meal plan sample — PantryChef",
                             body, purpose="outreach")
        if result.get("status") == "sent":
            lead["trial_sent"] = datetime.now().isoformat()
            sent += 1
    storage.save("pc_leads.json", leads)
    return {"outreach_sent": sent}


def run_full_cycle() -> dict:
    a = acquire_cycle()
    f = fulfill_cycle()
    rev = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("pc_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        outreach_sent=a["outreach_sent"],
        fulfillment_sent=f["fulfillment_sent"],
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
        total_revenue=rev["total_paid"],
    )
    return {**a, **f, **rev}
