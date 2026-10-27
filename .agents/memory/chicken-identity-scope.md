---
name: Chicken identity scope
description: Identity guarantees and constraints for ChickenSee tracking across video files.
---

Cross-video numeric identities are assigned only when the tracker confirms one of the five existing colored leg-band labels. The flock must use each color for at most one individual. ByteTrack IDs and short-gap proximity relinks are local to a video; unbanded birds must not be presented as confidently identified across separate clips.

**Why:** ByteTrack has no appearance embedding and its track IDs reset at video boundaries. Position-only matching in a crowded flock can merge similar birds, so an uncertain guess is worse than a new/local ID.

**How to apply:** Keep the unique-band constraint explicit in tracker changes and user-facing reports. If broader identity is needed, add a trained appearance-ReID system only after collecting identities across days/cameras and measuring identity switches and IDF1/HOTA on held-out recordings.
