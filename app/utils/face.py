"""
utils/face.py — Face Recognition Utility
==========================================
Pure computer-vision utility — no Flask, no db needed.
IMPORTS FROM: nothing internal (Level 0 in the hierarchy)
"""

import cv2
import face_recognition
import numpy as np
from concurrent.futures import ThreadPoolExecutor

# Global ThreadPoolExecutor for CPU-bound face recognition tasks.
# We limit max_workers to prevent CPU exhaustion on simultaneous uploads.
face_executor = ThreadPoolExecutor(max_workers=2)



def _bbox_from_face_location(top, right, bottom, left):
    return {
        'top': int(top),
        'right': int(right),
        'bottom': int(bottom),
        'left': int(left),
    }


def process_and_annotate_faces(image_bytes, known_faces_encodings, known_faces_data):
    """
    Process an image, identify known faces, and annotate with rectangles + names.

    Args:
        image_bytes: Raw bytes of the uploaded image.
        known_faces_encodings: List of numpy face encoding arrays.
        known_faces_data: List of (roll_number, name) tuples matching encodings.

    Returns:
        Tuple of (detections_list, annotated_image_or_None, face_count_int)
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return set(), None, 0

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        locs = face_recognition.face_locations(rgb_img)
        encs = face_recognition.face_encodings(rgb_img, locs)

        detections = []
        faces_count = len(locs)

        for (top, right, bottom, left), enc in zip(locs, encs):
            matches = face_recognition.compare_faces(known_faces_encodings, enc, tolerance=0.5)
            name = "Unknown"
            roll = None
            if True in matches:
                dists = face_recognition.face_distance(known_faces_encodings, enc)
                best_idx = np.argmin(dists)
                if matches[best_idx]:
                    roll, name = known_faces_data[best_idx]
                    detections.append({
                        'roll_number': roll,
                        'name': name,
                        'bounding_box': _bbox_from_face_location(top, right, bottom, left)
                    })

            # Draw bounding box and label
            cv2.rectangle(img, (left, top), (right, bottom), (0, 255, 0), 2)
            cv2.rectangle(img, (left, bottom - 35), (right, bottom), (0, 255, 0), cv2.FILLED)
            font = cv2.FONT_HERSHEY_DUPLEX
            display = f"{name} ({roll})" if roll else "Unknown"
            cv2.putText(img, display, (left + 6, bottom - 6), font, 0.7, (255, 255, 255), 1)

        return detections, img, faces_count

    except Exception as e:
        print(f"Face Process Error: {e}")
        return [], None, 0
