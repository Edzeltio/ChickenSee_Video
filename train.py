import os
import yaml
import shutil
import random
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
project_dir  = os.path.dirname(os.path.abspath(__file__))
dataset_dir  = os.path.join(project_dir, "dataset")
images_dir   = os.path.join(dataset_dir, "images")
labels_dir   = os.path.join(dataset_dir, "labels")

train_img_dir = os.path.join(dataset_dir, "train", "images")
train_lbl_dir = os.path.join(dataset_dir, "train", "labels")
val_img_dir   = os.path.join(dataset_dir, "val",   "images")
val_lbl_dir   = os.path.join(dataset_dir, "val",   "labels")

# ---------------------------------------------------------------------------
# Class definitions — must match Label Studio export order exactly
# 9 classes from current Label Studio project (alphabetical YOLO export order)
# ---------------------------------------------------------------------------
CLASS_NAMES = {
    0: "Chicken",
    1: "Drinker",
    2: "Feeder",
    3: "Leg_Band_Green",    # → Hen1
    4: "Leg_Band_Pink",     # → Hen5
    5: "Leg_Band_Violet",   # → Hen3
    6: "Leg_Band_White",    # → Hen2
    7: "Leg_Band_Yellow",   # → Hen4
    8: "Rooster"
}

# ---------------------------------------------------------------------------
# Step 1 — Auto train/val split (80/20) if not already done
# ---------------------------------------------------------------------------
def split_dataset(val_ratio=0.20, seed=42):
    """Split flat dataset/images + dataset/labels into train/val folders."""
    for d in [train_img_dir, train_lbl_dir, val_img_dir, val_lbl_dir]:
        os.makedirs(d, exist_ok=True)

    # Collect images that have a matching label file
    all_images = [
        f for f in os.listdir(images_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    paired = []
    for img_name in all_images:
        stem = os.path.splitext(img_name)[0]
        lbl_path = os.path.join(labels_dir, stem + ".txt")
        if os.path.isfile(lbl_path):
            paired.append((img_name, stem + ".txt"))
        else:
            print(f"[WARN] No label found for {img_name} — skipping")

    if not paired:
        raise FileNotFoundError(
            f"No paired image+label files found in {images_dir} / {labels_dir}"
        )

    random.seed(seed)
    random.shuffle(paired)

    n_val   = max(1, int(len(paired) * val_ratio))
    val_set = set(p[0] for p in paired[:n_val])

    copied_train = copied_val = 0
    for img_name, lbl_name in paired:
        src_img = os.path.join(images_dir, img_name)
        src_lbl = os.path.join(labels_dir, lbl_name)
        if img_name in val_set:
            shutil.copy(src_img, os.path.join(val_img_dir,   img_name))
            shutil.copy(src_lbl, os.path.join(val_lbl_dir,   lbl_name))
            copied_val += 1
        else:
            shutil.copy(src_img, os.path.join(train_img_dir, img_name))
            shutil.copy(src_lbl, os.path.join(train_lbl_dir, lbl_name))
            copied_train += 1

    print(f"✅ Split complete → train: {copied_train}  val: {copied_val}")
    return copied_train, copied_val


# Only split if train/val folders are empty or missing
need_split = (
    not os.path.isdir(train_img_dir)
    or len(os.listdir(train_img_dir)) == 0
)

if need_split:
    print("📂 Splitting dataset into train/val (80/20)...")
    split_dataset()
else:
    n_train = len(os.listdir(train_img_dir))
    n_val   = len(os.listdir(val_img_dir))
    print(f"📂 Using existing split → train: {n_train}  val: {n_val}")

# ---------------------------------------------------------------------------
# Step 2 — Write dataset_config.yaml
# ---------------------------------------------------------------------------
data_config = {
    "path":  dataset_dir.replace("\\", "/"),
    "train": "train/images",
    "val":   "val/images",
    "nc":    len(CLASS_NAMES),
    "names": CLASS_NAMES,
}

yaml_path = os.path.join(project_dir, "dataset_config.yaml")
with open(yaml_path, "w") as f:
    yaml.dump(data_config, f, default_flow_style=False, sort_keys=False)
print(f"✅ dataset_config.yaml written → {yaml_path}")

# Remove stale cache so Ultralytics re-scans labels
for cache in ["labels.cache", "train/labels.cache", "val/labels.cache"]:
    cache_path = os.path.join(dataset_dir, cache)
    if os.path.isfile(cache_path):
        os.remove(cache_path)
        print(f"🗑️  Removed stale cache: {cache_path}")

# ---------------------------------------------------------------------------
# Step 3 — Load YOLOv26 Nano base model
# ---------------------------------------------------------------------------
base_model = os.path.join(project_dir, "yolo26n.pt")
if not os.path.isfile(base_model):
    raise FileNotFoundError(f"Base model not found: {base_model}")

print(f"\n🚀 Fine-tuning YOLOv26 Nano: {base_model}")
print(f"   Classes ({len(CLASS_NAMES)}): {list(CLASS_NAMES.values())}\n")
model = YOLO(base_model)

# ---------------------------------------------------------------------------
# Step 4 — Train
# ---------------------------------------------------------------------------
results = model.train(
    data=yaml_path,
    epochs=150,
    imgsz=640,
    batch=4,              # safe for most GPUs; increase if VRAM allows
    close_mosaic=15,      # disable mosaic in last 15 epochs for clean boxes
    name="poultry_v5",
    exist_ok=True,
    project=os.path.join(project_dir, "runs", "detect"),
    # Augmentation — helps with small Leg_Band objects
    degrees=10.0,
    fliplr=0.5,
    mosaic=1.0,
    scale=0.9,            # INCREASED to 0.9 to force severe downscaling of crops
    hsv_h=0.015,
    hsv_s=0.7,
    hsv_v=0.4,
    patience=30,          # early stopping if no improvement for 30 epochs
    save_period=25,       # checkpoint every 25 epochs
    verbose=True,
)

# ---------------------------------------------------------------------------
# Step 5 — Copy best weights to project root
# ---------------------------------------------------------------------------
best_weights  = os.path.join(
    project_dir, "runs", "detect", "poultry_v5", "weights", "best.pt"
)
target_weights = os.path.join(project_dir, "custom_poultry.pt")

if os.path.isfile(best_weights):
    shutil.copy(best_weights, target_weights)
    print(f"\n🎉 Training complete!")
    print(f"   Best weights → {target_weights}")
else:
    print("\n❌ Training finished but best.pt not found — check runs/ folder.")