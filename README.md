# Wyze Live Auto Refresh

A Windows Python monitor for Wyze Web View. It checks up to nine camera tiles, attempts to recover offline feeds, and reloads the page when the timestamp region of a feed stops changing.

- Labels camera positions from **Top Left** through **Bottom Right** using a fixed 3×3 grid.
- Compares pixels in the lower-right corner of each tile to detect frozen feeds. The script uses OCR for visible error messages.
- Tries a nearby camera **Refresh** control before reloading the dashboard for persistent tile errors.
- Recognizes the **Log in Wyze Web Portal** screen and can fill the Wyze login form if credentials are configured. Complete any verification prompts in Chrome.

## Setup on Windows

Install Google Chrome, Python 3.8 through 3.12, and the Tesseract OCR executable with English language data. The Python package `pytesseract` calls the installed Tesseract program.

Open PowerShell in the folder containing the script. These commands use Python 3.8. Substitute your installed version, such as `-3.12`, if needed.

```powershell
py -3.8 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item WyzeLiveAutoRefresh.example.json WyzeLiveAutoRefresh.json
```

Open `WyzeLiveAutoRefresh.json` and check `tesseract_path` and `chrome.profile_path`. Use a dedicated Chrome profile directory, such as the example path, and close any other Chrome windows using that directory. You can keep the authentication fields blank and sign in manually in the Chrome window launched by the script. To enable automatic form filling, add your email and password to the local JSON file. These values are stored as plain text.

```powershell
.\.venv\Scripts\python.exe WyzeLiveAutoRefresh.py
```

Press **Ctrl+C** to stop. Status and recovery activity are recorded in `wyze-monitor.log`, `wyze-refresh.log`, and `wyze-console.log` as applicable.

## Configuration

| Setting | Purpose |
| --- | --- |
| `check_seconds` | Delay between checks. The default is 10 seconds, plus processing time. |
| `frozen_seconds` | How long the timestamp region may remain unchanged before a page reload. Defaults to 300 seconds. |
| `recovery_seconds` | Time allowed for streams to reconnect after startup or a page reload. |
| `grid` | Fractional bounds and gaps of the camera grid in the visible viewport. Adjust the starter bounds to match your dashboard. |
| `timestamp_pixels` | Fractional crop within each tile and pixel-change thresholds. Enable `save_debug_crops` temporarily to check the crop. |
| `error_phrases` | Lowercase phrases checked against visible page text and OCR output. |

If a camera appears as **NOT VISIBLE**, make sure the camera tiles fit within the browser viewport and adjust the grid bounds. If a working stream shows as **FROZEN**, inspect its debug crop to confirm that it contains the changing timestamp. For repeated OCR errors, confirm the configured executable path. If Chrome reports a profile conflict, close other Chrome processes using the monitor's profile before restarting the script.

## Sharing on GitHub

In GitHub Desktop, choose **File → Add Local Repository**, select this folder, then click **Publish repository**. Leave **Keep this code private** selected to limit access. The example JSON contains blank credentials. Keep the real JSON, browser profiles, logs, and debug screenshots out of commits. If you store a Chrome profile inside the repository with a name other than `ChromeProfile`, add that directory to `.gitignore` before committing. Review the **Changes** list in GitHub Desktop or run `git status --short` before your first commit.

Suggested repository description: **Monitor Wyze Web View feeds and refresh offline or frozen camera streams.**
