# 🐔 ChickenSee — OptiPlex Server Integration & Setup Guide

A complete, step-by-step guide to installing, configuring, and running the **ChickenSee Vision & Telemetry System** on your local Dell OptiPlex server and integrating it seamlessly with your Vercel cloud portal.

---

## 🏗️ Architecture Overview

```mermaid
flowchart TD
    subgraph OptiPlex["🖥️ Local OptiPlex Server (Windows 10/11)"]
        Cam["📹 Farm Cameras / Recorders"] -->|"Save MP4s"| WatchFolders["📁 Watched Video Folders"]
        Sensors["🌡️ Environmental Sensors"] -->|"Log Telemetry"| SQLite["sensors.sqlite"]
        
        WatchFolders -->|"Detects stable files (>20s)"| Worker["⚙️ Video Analyzer Daemon\n(YOLO26n + ByteTrack)"]
        SQLite -->|"Pairs intervals"| Worker
        
        Worker -->|"Generates"| LocalResults["💾 C:\\ChickenSee_Local_Storage\n• /results (JSON)\n• /videos (_detected.mp4)\n• queue_status.json"]
        
        API["🔌 Local Analytics API\n(FastAPI :8000)"] <--> Worker
        GUI["🖥️ ChickenSee Control Center\n(Desktop App)"] <--> API
        
        Watcher["🔄 Sync Agent\n(Node.js / Chokidar)"] -->|"Watches result JSON & SQLite"| LocalResults
    end

    subgraph Vercel["☁️ Vercel Cloud Portal"]
        Watcher -->|"HTTPS POST /api/sync\n(Bearer Sync Secret)"| Supabase["🗄️ Private Supabase Storage\n• /results/*.json\n• videos remain local\n• /results/queue_status.json\n• /databases/*.sqlite"]
        Supabase --> WebApp["🌐 ChickenSee Synced Portal\n• Detection Queue (Live Status)\n• Behavior Analytics & KPIs\n• SQLite Viewer"]
    end
```

---

## 📋 Prerequisites on the OptiPlex

Before starting, ensure your Dell OptiPlex has the following installed:

1. **Operating System**: Windows 10 or 11 (64-bit).
2. **Python**: Python 3.10, 3.11, or 3.12:
   - ⚠️ **Important during Python installation**: Check the box **"Add Python to PATH"**.
   - Verify `python.exe` and `pythonw.exe` are available in PowerShell:
     ```powershell
     python --version
     ```
3. **Node.js**: Node.js 18, 20, or newer (LTS recommended):
   - [Download Node.js](https://nodejs.org/)
   - Verify in PowerShell:
     ```powershell
     node --version
     npm --version
     ```
4. **Git**: [Download Git for Windows](https://git-scm.com/) (optional, but recommended).
5. **Model Weights**: Ensure `custom_poultry.pt` is in the repository root folder.

---

## 🚀 Step 1: Clone or Copy the Repository

Place the project on your OptiPlex (e.g., in `C:\Users\<YourUser>\Documents\ChickenSee_Videos(sql)` or `C:\ChickenSee`):

```powershell
cd C:\Users\<YourUser>\Documents\ChickenSee_Videos(sql)
```

Confirm that `custom_poultry.pt` and `favicon.ico` are in the folder:
```powershell
Test-Path custom_poultry.pt
Test-Path favicon.ico
```

---

## 📦 Step 2: Install Local Dependencies

Open **PowerShell** in the repository root directory and run:

```powershell
# 1. Install Python analytics & AI packages
python -m pip install --upgrade pip
python -m pip install -r analytics\requirements.txt

# 2. Install Sync Agent Node.js dependencies
Push-Location agent
npm ci
Pop-Location
```

> **Tip:** You can also click the **"Initialize / repair"** button in the Desktop App to do this automatically.

---

## 🖥️ Step 3: Launch ChickenSee Desktop Control Center

Launch the graphical Control Center app:

```powershell
python startup\chickensee_desktop.py
```

You will see the branded **ChickenSee Control Center** with the official rooster icon and live system tabs:
- **Dashboard**: Start/Stop services, process status, quick video test.
- **Queue**: Real-time detection queue, progress bar, frame counts, and cancel button.
- **Setup**: Folder configuration, local storage path, and Vercel cloud sync.

---

## ⚙️ Step 4: Configure Setup (Watch Folders & Storage)

In the Control Center, click on the **Setup** tab:

1. **Video Folders to Watch**:
   - Click **"Add folder…"** and select the folder(s) where your cameras or DVR save MP4 recordings.
   - *Example:* `D:\CameraRecordings` or `C:\FarmVideos`.
2. **Local Results and Logs**:
   - Default: `C:\ChickenSee_Local_Storage`
   - Keeps raw footage and generated `videos/` (`_detected.mp4`) local; stores `results/` (JSON), queue status, and logs locally.
3. **Optional Vercel Cloud Sync**:
   - Check the box: **"Sync analysis results, queue status, and databases to Supabase (videos stay local)"**.
   - **Sync URL**: Enter your deployed Vercel domain followed by `/api/sync`:
     ```text
     https://your-chickensee-portal.vercel.app/api/sync
     ```
   - **Sync Secret**: Enter the same secret configured on your Vercel project environment variables (e.g. `SYNC_SECRET`).
4. Click **"Save settings"**.

---

## ☁️ Step 5: Connect Vercel to Supabase

In your [Vercel Dashboard](https://vercel.com/dashboard) under **Settings → Environment Variables**, set the following for Production (and Preview if used). Redeploy after changing them.

| Variable | Description | Example |
|---|---|---|
| `SUPABASE_URL` | Project URL shown in Supabase API settings | `https://stovfnbqgufxtpaoznbv.supabase.co` |
| `SUPABASE_STORAGE_BUCKET` | Private bucket created for sync | `chickensee-sync` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase server-only service-role key; never expose to browser or desktop agent | Set directly in Vercel; do not commit |
| `SYNC_SECRET` | Secret key matching the OptiPlex desktop app | Generate a high-entropy value |
| `DASHBOARD_PASSWORD` | Password you use to log in to the web portal | `farm-admin-2026` |
| `JWT_SECRET` | 32-character random string for session tokens | `a-secure-random-32-char-string` |

The desktop agent only needs the deployed Vercel `/api/sync` URL and matching `SYNC_SECRET`; it never receives Supabase credentials. Supabase project creation and storage are separate from existing Vercel Blob data—old Blob objects are not copied automatically.

The analytics API is the single writer for `results/queue_status.json`; the Control Center reads the same live `/queue` API and must not overwrite that snapshot. The sync agent prioritizes queue snapshots and the `live_detection.jpg` preview ahead of large database uploads. Vercel reports the queue as delayed if it has not received a desktop heartbeat for 15 seconds. Keep Cloud Sync enabled and all three desktop services running for live cloud queue updates.

---

## 🧪 Step 6: Test Video Analysis & Live Queueing

1. Go to the **Dashboard** tab in ChickenSee Control Center and click **"▶ Start ChickenSee"**.
2. Verify all three services turn **green**:
   - 🟢 **Folder watcher**: Running
   - 🟢 **Analytics API**: Running (port 8000)
   - 🟢 **Video analyzer**: Running
3. Test a sample video:
   - Under **Quick video test**, select your watch folder and click **"Choose a video and test"**.
   - Select any short MP4 video. A copy is placed in your watch folder.
4. Switch to the **Queue** tab in the desktop app:
   - When the file finishes writing and stays stable for ~20s, it enters **PROCESSING**.
   - You will see the active video name, live progress bar, and frame counts.
5. Open your **Vercel Web Portal** (`https://your-chickensee-portal.vercel.app`):
   - In the top header next to "Optiplex Live", observe the live badge:
     `● Queueing: chickensee-test... (45%)`
   - Click the **"Detection Queue"** tab to view the live progress, active video name, and queued items without streaming raw videos!

---

## 🔄 Step 7: Enable 24/7 Autostart on Boot

To make sure ChickenSee automatically runs whenever the OptiPlex boots up:

1. In ChickenSee Control Center, go to the **Setup** tab.
2. Click **"Enable startup"**.
3. This registers:
   - A **ChickenSee Control Center** shortcut on your Windows Desktop (with the official icon).
   - The `startup\start-chickensee.bat` launcher in Windows Startup (`ChickenSee.lnk`) and the current user's Run key. It starts the background supervisor, which launches the watcher, API, and analyzer when your Windows user signs in.

> **To verify autostart:** Restart the OptiPlex and sign in. Within 15 seconds, the services will start automatically in the background.

---

## 📁 Key File Locations on OptiPlex

| Item | Location on OptiPlex |
|---|---|
| **Desktop App Shortcut** | `Desktop\ChickenSee Control Center.lnk` |
| **Startup Shortcut** | `shell:startup\ChickenSee.lnk` |
| **Local Results (JSON)** | `C:\ChickenSee_Local_Storage\results\` |
| **Annotated Videos** | `C:\ChickenSee_Local_Storage\videos\` |
| **Queue Status Payload** | `C:\ChickenSee_Local_Storage\results\queue_status.json` |
| **Service Logs** | `C:\ChickenSee_Local_Storage\logs\` |
| **Watch Folder Config** | `<RepoRoot>\agent\directories.json` |
| **Sync Credentials** | `<RepoRoot>\agent\.env` *(automatically ignored by Git)* |

---

## 🛠️ Maintenance & Troubleshooting

- **Queue says "Offline / Services Stopped"**:
  Open the Control Center desktop shortcut and click **"▶ Start ChickenSee"** on the Dashboard.
- **Videos are not being analyzed**:
  Ensure the video is in an MP4 container and has been stable (unchanged file size) for at least 20 seconds.
- **Cloud Uploads not showing on Vercel**:
  Confirm that **Cloud Sync** is toggled ON in the Setup tab and that your `SYNC_SECRET` matches your Vercel environment variable exactly.
  Check that `SUPABASE_URL`, `SUPABASE_STORAGE_BUCKET`, and `SUPABASE_SERVICE_ROLE_KEY` are configured in Vercel and redeploy. Pending local files remain queued and the agent retries cloud sync.
- **View Live Logs**:
  Click **"Open logs"** on the Dashboard to inspect `worker.log`, `watcher.log`, or `api.log`.
- **API Documentation**:
  Visit `http://127.0.0.1:8000/docs` in your browser on the OptiPlex to interactively query the local FastAPI service.
