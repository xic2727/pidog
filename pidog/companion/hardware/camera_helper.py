import logging
from typing import Optional, Any
import io

logger = logging.getLogger(__name__)


class CameraHelper:
    """
    On-demand camera capture helper supporting vilib, cv2, and picamera2.
    Provides graceful fallback to None if hardware/libraries are unavailable or capture fails.
    """

    def __init__(self, camera_backend: str = "auto", device_index: int = 0):
        """
        :param camera_backend: 'auto', 'vilib', 'cv2', 'picamera2'
        :param device_index: camera device index for cv2 (e.g. 0)
        """
        self.camera_backend = camera_backend
        self.device_index = device_index
        self._cv2_cap = None
        self._picam2 = None

    def capture_jpeg(self, quality: int = 80) -> Optional[bytes]:
        """
        Capture a frame and return raw JPEG bytes. Returns None on failure or if unavailable.
        """
        if self.camera_backend in ("vilib", "auto"):
            jpeg = self._capture_vilib()
            if jpeg is not None:
                return jpeg
            if self.camera_backend == "vilib":
                return None

        if self.camera_backend in ("picamera2", "auto"):
            jpeg = self._capture_picamera2(quality=quality)
            if jpeg is not None:
                return jpeg
            if self.camera_backend == "picamera2":
                return None

        if self.camera_backend in ("cv2", "auto"):
            jpeg = self._capture_cv2(quality=quality)
            if jpeg is not None:
                return jpeg
            if self.camera_backend == "cv2":
                return None

        return None

    def _capture_vilib(self) -> Optional[bytes]:
        """Attempt capture using SunFounder vilib library."""
        try:
            from vilib import Vilib
            # Check if vilib has frame data available
            if hasattr(Vilib, "img_encoded") and Vilib.img_encoded is not None:
                return bytes(Vilib.img_encoded)
            elif hasattr(Vilib, "raw_data") and Vilib.raw_data is not None:
                # If cv2 is available, encode raw_data
                try:
                    import cv2
                    success, enc = cv2.imencode('.jpg', Vilib.raw_data)
                    if success:
                        return enc.tobytes()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"vilib capture failed or unavailable: {e}")
        return None

    def _capture_picamera2(self, quality: int = 80) -> Optional[bytes]:
        """Attempt capture using libcamera Picamera2."""
        try:
            from picamera2 import Picamera2
            if self._picam2 is None:
                self._picam2 = Picamera2()
                self._picam2.start()

            buf = io.BytesIO()
            self._picam2.capture_file(buf, format="jpeg")
            return buf.getvalue()
        except Exception as e:
            logger.debug(f"Picamera2 capture failed or unavailable: {e}")
        return None

    def _capture_cv2(self, quality: int = 80) -> Optional[bytes]:
        """Attempt capture using OpenCV VideoCapture."""
        try:
            import cv2
            opened_here = False
            if self._cv2_cap is None:
                self._cv2_cap = cv2.VideoCapture(self.device_index)
                opened_here = True

            if not self._cv2_cap.isOpened():
                if opened_here:
                    self._cv2_cap = None
                return None

            ret, frame = self._cv2_cap.read()
            if not ret or frame is None:
                return None

            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            ret, enc = cv2.imencode('.jpg', frame, encode_param)
            if ret:
                return enc.tobytes()
        except Exception as e:
            logger.debug(f"cv2 capture failed or unavailable: {e}")
        return None

    def close(self):
        """Release camera resources safely."""
        try:
            if self._cv2_cap is not None:
                self._cv2_cap.release()
                self._cv2_cap = None
        except Exception as e:
            logger.debug(f"Error releasing cv2 camera: {e}")

        try:
            if self._picam2 is not None:
                self._picam2.stop()
                self._picam2.close()
                self._picam2 = None
        except Exception as e:
            logger.debug(f"Error closing Picamera2: {e}")
