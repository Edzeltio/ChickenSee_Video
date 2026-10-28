# ChickenSee Poultry Tracking and Dataset Guide

This guide describes the implemented Python pipeline, how to collect and train data, and what the current identity logic can reliably claim.

## System assessment

The project has two Python tracking paths:

- `analytics/worker.py` analyzes stable MP4 files in configured folders. `analytics/tracker.py` uses YOLO detections and ByteTrack; the worker resets tracker state at each video boundary.
- `edge/pipeline.py` processes a continuous local camera/RTSP stream or one selected video. `edge/detector.py` uses the edge configuration and ByteTrack.
- `train.py` trains the nine-class detector. It now validates labels, keeps source-video groups together by default, and does not start training on import.
- `analytics/collect_dataset.py` collects full frames, YOLO-format label proposals, object crops, and JSONL manifests. It supports reviewed manual labels without weights and model-assisted proposals when compatible custom weights are available.

ByteTrack is appropriate for short-term association in this controlled, fixed-camera setting: it is light enough for a CPU edge machine and uses motion/box overlap without another large model. It does not provide identity across separate video files. Pure proximity-based relinking also risks merging two similar birds, so the analytics tracker only relinks mutually closest short-gap candidates when the distance is unambiguous.

For cross-video individuals, a detected uniquely assigned colored leg band is the current stable identity signal. The five existing colors map deterministically to IDs `1000001`–`1000005`. This assumes one hen per band color. If multiple hens share a color, or a band is hidden or missed, the system cannot guarantee individual identity. Unbanded chickens, roosters, and all ByteTrack IDs without a confirmed band remain local to the video. A learned appearance Re-ID model is not currently present; adding one requires labeled identities across multiple days and cameras, followed by a measured validation set.

## Install and configure

Use Python 3.10–3.12 on a Windows, Linux, or macOS computer that can access the videos and camera. CPU inference is the portable baseline. CUDA/ROCm GPU inference or Apple MPS is selected automatically when the installed PyTorch build exposes it:

```bash
python -m pip install --upgrade pip
python -m pip install -r analytics/requirements.txt
python setup_check.py
```

For NVIDIA or AMD GPU acceleration, install a PyTorch build compatible with that machine's GPU runtime before running `setup_check.py`. Set `INFERENCE_DEVICE=cpu` to force CPU, `INFERENCE_DEVICE=0` (or another available index) to choose a CUDA/ROCm device, or `INFERENCE_DEVICE=mps` to request Apple MPS. `auto` prefers CUDA/ROCm, then MPS, then CPU. Intel accelerators and unsupported PyTorch backends are not automatically selected.

## Install or update model weights

Install a new model from any location with one command. This copies it into `models/` under a versioned filename, records its checksum, activates it, and keeps previous versions:

```bash
python model_manager.py list
python model_manager.py install "D:/Downloads/chickensee-v2.pt"
```

If you copied a file into `models/` yourself, activate the newest one or select a specific version:

```bash
python model_manager.py activate --latest
python model_manager.py activate models/chickensee-v1.pt
```

The manager writes an atomic `models/active.json` pointer and records the version and SHA-256 checksum. It never overwrites or deletes an existing version. `python model_manager.py list` compares the active file to the recorded checksum.

```bash
python model_manager.py activate models/chickensee-v1.pt
```

`MODEL_PATH` is an explicit override and takes priority over the registry. Clear that environment variable if you want the manager-selected model to take effect. Restart any running worker/service after activation. Weights must use the exact nine classes in the order produced by `train.py`: Chicken, Drinker, Feeder, Leg_Band_Green, Leg_Band_Pink, Leg_Band_Violet, Leg_Band_White, Leg_Band_Yellow, Rooster. Both inference paths reject incompatible class metadata before processing frames.

Training saves a timestamped file under `models/` and automatically activates it. This preserves the previous model instead of overwriting `custom_poultry.pt`. Existing `custom_poultry.pt` installations remain a fallback until an active registry is created.

The analytics worker reads these environment variables:

| Variable | Default | Purpose |
|---|---:|---|
| `MODEL_PATH` | Active model registry, then `<project>/custom_poultry.pt` | Explicit weights override |
| `INFERENCE_DEVICE` | `auto` | `auto`, `cpu`, `mps`, or CUDA/ROCm device index such as `0` |
| `TRACKER_CONFIG` | `bytetrack_poultry.yaml` | ByteTrack settings |
| `CONF_THRESHOLD` | `0.15` | Detection floor; keep at or below `track_low_thresh` to preserve low-score recovery |
| `IOU_THRESHOLD` | `0.45` | Detector NMS overlap threshold |
| `INFERENCE_SIZE` | `640` | Inference image size; larger helps small bands but costs more compute |
| `FRAME_STRIDE` | `2` | Analyze every Nth frame; `1` improves temporal sampling at roughly twice the inference work |
| `CHUNK_MINUTES` | `30` | Analytics/report interval length |
| `VIDEO_INPUT_DIR` | `agent/data` | Default watched-video folder |
| `FILE_STABILITY_THRESHOLD_S` | `2` | Required unchanged time before a video is queued |
| `ANALYTICS_API_HOST` | `127.0.0.1` | Local-only API binding |
| `ANALYTICS_API_PORT` | `8000` | Local API port |

The separate `edge.pipeline` uses these environment variables from `edge/config.py`:

| Variable | Default | Purpose |
|---|---:|---|
| `VIDEO_SOURCE` | A project-local MP4 path that may not exist | Default source; pass `--source` to select a file, camera index, or stream explicitly |
| `MODEL_PATH` | Active model registry, then `<project>/custom_poultry.pt` | Explicit weights override |
| `INFERENCE_DEVICE` | `auto` | `auto`, `cpu`, `mps`, or CUDA/ROCm device index |
| `TRACKER_CONFIG` | `bytetrack_poultry.yaml` | Tracker thresholds and buffer |
| `INFERENCE_SIZE` | `640` | YOLO input size |
| `CONF_THRESHOLD` | `0.15` | Detection floor |
| `IOU_THRESHOLD` | `0.45` | Detector NMS threshold |
| `TARGET_WIDTH` | `640` | Downscale wider frames before inference; output boxes are scaled back |
| `CPU_THREADS` | Available CPU count | PyTorch CPU threads when using CPU inference |
| `FPS_TARGET` | `15` | Annotated video output frame rate |
| `RECORD_ANNOTATED` | `1` | Write annotated edge output unless `--no-record` |
| `SENSOR_SOURCE` | `simulation` | Implemented choices are `simulation` and `serial`; MQTT/HTTP adapters are not implemented |
| `SENSOR_SERIAL_PORT` / `SENSOR_BAUD` | `COM3` / `9600` | Serial sensor connection |

For Arduino/USB/RS-485 serial input, install `pyserial`, set `SENSOR_SOURCE=serial`, and send one newline-terminated JSON object or CSV row per sample. Both formats require six finite values: temperature °C, relative humidity %, ammonia ppm, lux, hopper load kg, and water flow L/min. JSON example: `{"temp":28.5,"hum":68.2,"nh3":12.4,"lux":350,"hopper":40.0,"water":0.5}`. CSV uses the same order. Invalid or missing hardware samples are logged and omitted; they are not replaced with simulated values. Hourly exports mark absent sensor data as unavailable; zero values in the legacy-compatible SQLite numeric columns must be ignored when `sensor_data_available` is false. Existing databases gain this flag without deleting rows.

`edge.pipeline` also reads `START_HOUR`/`END_HOUR` (06:00/18:00 defaults), `FEEDER_PROXIMITY_PX`/`DRINKER_PROXIMITY_PX` (90), and `RESTING_VELOCITY_PX_S`/`AGITATED_VELOCITY_PX_S` (4/70). `--force-start` is for manual testing only. Sensor simulation is the default and should not be used to interpret production environmental measurements.

Behavior thresholds can be configured with `FEEDER_ZONE_PROXIMITY_PX` (95), `WATERER_ZONE_PROXIMITY_PX` (95), `INACTIVITY_VELOCITY_PX_S` (3.5), `INACTIVITY_DURATION_S` (180), `FEATHER_PECKING_PROXIMITY_PX` (60), `FEATHER_PECKING_APPROACH_PX_S` (22), and `FEATHER_PECKING_ESCAPE_PX_S` (18). These are image-pixel thresholds, not physical distances; calibrate them after confirming camera resolution, camera position, and observed motion. Avoid tuning solely against a short clip.

`bytetrack_poultry.yaml` starts with a high-score threshold of `0.40`, a low-score recovery threshold of `0.15`, a new-track threshold of `0.40`, and a `track_buffer` of 90 tracker updates. The analytics configuration now passes a `0.15` confidence floor by default so ByteTrack can see detections needed for its low-score recovery stage. With `FRAME_STRIDE=2` on 20 FPS footage, one tracker update represents about 0.1 seconds; the 90-update buffer is therefore about 9 seconds. Lower frame stride increases the real-time duration represented by a fixed frame buffer.

The analytics API defaults to localhost because its endpoints are not intended as an internet-facing service. Do not bind it to `0.0.0.0` or expose port 8000 outside a trusted local host without adding authentication and network controls.

## Collect and organize dataset examples

### Manually label a selected video or local camera

This mode does not require an existing detector. It opens sampled frames in an OpenCV window:

```bash
python -m analytics.collect_dataset --manual --input "D:/FarmVideos/day1.mp4" --sample-interval 2
python -m analytics.collect_dataset --manual --camera 0 --duration 120 --sample-interval 2
```

Press `R` to draw a box, enter a class name or class number in the terminal, then repeat for other objects in the frame. Press `S` to save the frame and YOLO labels, `N` to skip it, or `Q` to stop. For the camera, use `--duration` or `--max-frames` so collection ends deliberately.

### Collect detector-assisted crop proposals

With compatible poultry weights:

```bash
python -m analytics.collect_dataset --weights custom_poultry.pt --input "D:/FarmVideos/day1.mp4" "D:/FarmVideos/day2.mp4"
python -m analytics.collect_dataset --weights custom_poultry.pt --camera 0 --duration 120
```

Each run creates a timestamped folder containing:

- `images/<source-key>/` and `labels/<source-key>/`: sampled full frames and YOLO-format boxes.
- `crops/<class>/<source-key>/`: individual object crops.
- `manifests/<source-key>.jsonl`: source path, frame/time, tracker ID when available, class, confidence, box, and crop path.

Predicted labels are marked `auto_labeled: true`; they are proposals, not ground truth. Review and correct them before training or using them as an accuracy benchmark. `--crops-only` omits full-frame YOLO data. For a multi-day dataset, keep each source video in a separate group and collect different lighting, bird poses, occlusion, and camera conditions.

## Train

Use manual collection for the first dataset when no compatible custom model exists. Set `--dataset-dir` to the collection run directory, which contains `images/` and `labels/`:

```bash
python train.py --dataset-dir "dataset/collection/collection_YYYYMMDD_HHMMSS" --base-model yolo26n.pt --epochs 150 --image-size 640 --batch 4
```

By default, images in the same source subfolder are kept together during train/validation splitting. If the dataset is flat, training stops rather than silently putting near-duplicate frames from one recording into both splits. Prefer reorganizing by video or provide a `--group-regex` that captures the video key. `--random-split` is available only as an explicit opt-in and may overstate validation accuracy due to video-frame leakage.

The training script checks image/label pairs, class IDs, and normalized YOLO coordinates before training; existing nonempty splits are reused only when complete and valid, never overwritten. The nine class IDs must match the class order above. The best checkpoint is copied to `custom_poultry.pt`.

## Run and test

### Automatically analyze watched videos

Set the video folder and run:

```bash
# PowerShell
$env:VIDEO_INPUT_DIR = "D:\FarmVideos"
$env:MODEL_PATH = "C:\ChickenSee\custom_poultry.pt"
python -m analytics.worker
```

The worker ignores files still being written, then processes MP4s found under `VIDEO_INPUT_DIR`. For the portal/API workflow, start the local API separately with the existing startup script or run `python -m analytics.api`; the API and worker use the same configured local database.

### Analyze a selected file to an annotated video

```bash
python -m analytics.detect_sample_cli --input "D:/FarmVideos/day1.mp4" --output "D:/FarmResults/day1_detected.mp4" --stride 1
```

### Run a local camera or one selected video with the edge pipeline

```bash
python -m edge.pipeline --source 0 --force-start --no-record
python -m edge.pipeline --source "D:/FarmVideos/day1.mp4" --force-start --no-record
python -m edge.pipeline --source 0 --device cpu --model models/chickensee-v2.pt --force-start --no-record
```

`--source` accepts a local camera index, video path, or RTSP/HTTP stream. A finite video now stops at end-of-file instead of reopening and replaying forever. The edge pipeline's default schedule is 06:00–18:00 local time; `--force-start` bypasses it for testing. `--view` opens an OpenCV window and requires a desktop session.

### Tests

```bash
python -m unittest analytics.tests.test_training
python -m unittest analytics.tests.test_portable_setup
python -m unittest analytics.tests.test_legband
python -m unittest analytics.tests.test_analytics
python -m unittest startup.test_configuration
python -m py_compile train.py analytics/collect_dataset.py analytics/tracker.py edge/detector.py edge/pipeline.py model_registry.py device_utils.py
```

`python edge/test_pipeline.py` is an integration check that additionally requires active compatible weights, its configured sample video, and local write access for SQLite/reports. A successful unit suite does not establish tracking accuracy; accuracy requires annotated ground truth and metrics such as IDF1/HOTA, identity switches, detector precision/recall, and per-class AP.

## Sample-video result and current limits

The workspace contains `video_detected.mp4`: H.264, 640×526, 20 FPS, 861 frames, 43.05 seconds. It decodes cleanly with FFmpeg. A visual sample at 10 seconds shows an embedded overlay reporting 5 birds, 1 feeder, and 1 drinker; that is an observation of the old output, not a ground-truth score. There is no `custom_poultry.pt`, no training dataset, and no annotated identity ground truth in this workspace, and the Python vision dependencies are not installed in the current Replit runtime. Therefore YOLO inference, identity-switch counts, detection accuracy, and FPS on this clip could not be remeasured here.

Known limitations:

- Darkness, shadows, glare, motion blur, tiny birds, or low camera resolution reduce detection and leg-band confidence.
- Occlusion and close flocking can break ByteTrack tracks; the brief spatial relink is intentionally conservative but cannot guarantee identity.
- Camera movement invalidates the fixed pixel thresholds used by behavior analysis and weakens short-gap spatial association.
- Chickens with similar plumage cannot be safely re-identified across separate videos from the current detector alone.
- A hidden, dirty, or visually ambiguous colored band can prevent stable identity recognition. The current five color classes require distinct colors per individually tracked hen.
- Sparse/imbalanced training images or unreviewed auto-labels can reinforce model mistakes. Validation should split by source video and cover all classes and recording conditions.
- The local camera, CUDA acceleration, Arduino/serial sensors, and Windows desktop UI require access to the actual farm machine and hardware; none can be verified in the hosted environment. The serial payload and source behavior are unit-tested, but no physical Arduino was available.
- The Linux systemd example uses `/opt/chickensee` and a `chickensee` service account; edit those values for the installation path and non-root account created on the target machine. Resource limits are not hard-coded.

## Recommended first production values

Start with `CONF_THRESHOLD=0.15`, `track_high_thresh=0.40`, `track_low_thresh=0.15`, `new_track_thresh=0.40`, `FRAME_STRIDE=2`, and `INFERENCE_SIZE=640`. These align the detector floor with the configured ByteTrack recovery threshold and keep CPU inference load lower than full-rate processing. Compare against stride 1 on a manually labeled short clip before deploying; do not raise confidence to reduce false detections until recall and ID-switch effects are measured. Tune only after capturing representative day/night and occlusion cases.
