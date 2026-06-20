"""
VideoEditor — autonomous video polish + reels cutter.

Usage
-----
# Scan drop folder
python3 run_videoeditor_auto.py

# Process a local file
python3 run_videoeditor_auto.py --input /path/to/vid.mp4

# Download from YouTube, process, upload to Google Drive
python3 run_videoeditor_auto.py --youtube "https://youtube.com/watch?v=..."

# All flags together
python3 run_videoeditor_auto.py --youtube URL --drive --post

Env vars
--------
GDRIVE_FOLDER_ID   Google Drive folder ID for uploads
YT_AUTO_POST=1     Auto-post to YouTube after processing
YT_POST_MASTER=1   Also post the full master video
YT_PRIVACY=public  YouTube privacy setting
"""

import argparse
import os
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from autonomous.self_healing import with_healing
from paywall.agent_paywall import paywall_prompt
from videoeditor.tools import run_full_cycle

console = Console()
AGENT_KEY = "videoeditor"

GDRIVE_FOLDER_ID = "1eYrrQm6FVTTVzD3AhCpn7chcvnIHzZUO"  # user's Drive folder


@with_healing(AGENT_KEY)
def cycle(
    input_path: str | None = None,
    youtube_url: str | None = None,
    auto_post: bool = False,
    post_master: bool = False,
    drive_upload: bool = False,
) -> None:
    console.print(
        Panel(
            "[bold cyan]VideoEditor[/bold cyan]  —  Polish + Reels Cutter\n"
            "[dim]YouTube → Process → Google Drive[/dim]",
            border_style="cyan",
        )
    )

    # Download from YouTube if URL provided
    if youtube_url:
        console.print(f"  [cyan]Downloading:[/cyan] {youtube_url}")
        from videoeditor.yt_downloader import download_youtube
        dl = download_youtube(youtube_url)
        if "error" in dl:
            console.print(f"  [red]Download failed:[/red] {dl['error']}")
            return
        console.print(f"  [green]Downloaded:[/green] {dl['title']}")
        input_path = dl["path"]

    r = run_full_cycle(input_path=input_path, auto_post=auto_post, post_master=post_master)

    if r["processed"] == 0 and r["errors"] == 0:
        console.print("  [dim]No videos found.[/dim]")
        return

    console.print(
        f"  [green]Processed:[/green] {r['processed']}   "
        f"[red]Errors:[/red] {r['errors']}"
    )

    for meta in r.get("results", []):
        tbl = Table(show_header=False, box=None, padding=(0, 2))
        tbl.add_column(style="dim")
        tbl.add_column()

        tbl.add_row("slug", meta["slug"])
        tbl.add_row("duration", f"{meta['source_duration_s']} s")
        tbl.add_row("resolution", meta["source_resolution"])
        tbl.add_row("master", meta["master"])

        for reel in meta.get("reels", []):
            label = f"{reel['duration_s']}s reel"
            tbl.add_row(label, reel["file"])

        tbl.add_row("processing time", f"{meta['processing_time_s']} s")

        # Google Drive upload
        if drive_upload or os.getenv("GDRIVE_AUTO_UPLOAD", "0") == "1":
            console.print("  [cyan]Uploading to Google Drive…[/cyan]")
            from videoeditor.gdrive_uploader import upload_video_outputs
            dr = upload_video_outputs(meta, folder_id=GDRIVE_FOLDER_ID)
            for up in dr.get("uploads", []):
                fname = os.path.basename(up.get("local_file", ""))
                if up.get("status") == "uploaded":
                    tbl.add_row(f"Drive: {fname}", up.get("url", ""))
                else:
                    tbl.add_row(f"Drive: {fname}", f"[red]{up.get('error')}[/red]")

        # YouTube results
        for yt in meta.get("youtube", {}).get("youtube_posts", []):
            kind = "Short" if yt.get("is_short") else "Full video"
            if yt.get("status") == "uploaded":
                url = yt.get("shorts_url") if yt.get("is_short") else yt.get("url", "")
                cap_note = " + captions" if yt.get("captions", {}).get("status") == "uploaded" else ""
                tbl.add_row(f"YouTube {kind}", f"{url}{cap_note}")
            else:
                tbl.add_row(f"YouTube {kind}", f"[red]{yt.get('error', 'failed')}[/red]")

        console.print(tbl)
        console.print()


def main() -> None:
    p = argparse.ArgumentParser(description="VideoEditor — YouTube → Process → Google Drive")
    p.add_argument("--input", "-i", metavar="PATH",
                   help="Process a local video file")
    p.add_argument("--youtube", "-y", metavar="URL",
                   help="Download from YouTube, then process")
    p.add_argument("--drive", action="store_true", default=False,
                   help="Upload outputs to Google Drive after processing")
    p.add_argument("--post", action="store_true", default=False,
                   help="Auto-post reels to YouTube after processing")
    p.add_argument("--post-master", action="store_true", default=False,
                   help="Also post the full master video to YouTube")
    p.add_argument("--interval", type=int, default=0, metavar="MINUTES",
                   help="Re-scan data/ve_inputs/ every N minutes (0 = run once)")
    a = p.parse_args()

    if not paywall_prompt(AGENT_KEY):
        return

    while True:
        cycle(
            input_path=a.input,
            youtube_url=a.youtube,
            auto_post=a.post,
            post_master=a.post_master,
            drive_upload=a.drive,
        )
        if a.interval <= 0 or a.input or a.youtube:
            break
        console.print(f"  [dim]Next scan in {a.interval} min…[/dim]")
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
