# staff_identification


Staff Identification with Tiny YOLOv7

**Overview**
This repository outlines a process to identify staff members wearing name tags in videos captured by a 3D sensor. Utilizing Tiny YOLOv7, it involves two models: person detection and name tag detection. Valid staff identifications are determined by checking if the name tag bounding box is contained within the person bounding box.

**Steps**
Data Processing: From video, extract frames, annotate images, and augment dataset.
Model Training: Employ transfer learning with Tiny YOLOv7 architecture.
Evaluation: Assess model performance using precision, recall, and mAP metrics.
Visualization: Utilize graphs for performance visualization.

**Conclusion**
The process enables efficient staff identification in videos, enhancing security and management systems.