"""Common image processing utility functions."""
import cv2
import numpy as np


class ImageUtils:
    """Static utility methods for image processing."""

    @staticmethod
    def bgr_to_gray(img: np.ndarray) -> np.ndarray:
        if len(img.shape) == 2:
            return img
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def bgr_to_hsv(img: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    @staticmethod
    def bgr_to_rgb(img: np.ndarray) -> np.ndarray:
        return img[:, :, ::-1]

    @staticmethod
    def rgb_to_gray(img: np.ndarray) -> np.ndarray:
        if len(img.shape) == 2:
            return img
        return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    @staticmethod
    def threshold(img: np.ndarray, thresh: int = 127, max_val: int = 255,
                  method: str = 'binary') -> np.ndarray:
        """Apply threshold to grayscale image."""
        if len(img.shape) == 3:
            img = ImageUtils.bgr_to_gray(img)
        method_map = {
            'binary': cv2.THRESH_BINARY,
            'binary_inv': cv2.THRESH_BINARY_INV,
            'trunc': cv2.THRESH_TRUNC,
            'tozero': cv2.THRESH_TOZERO,
            'otsu': cv2.THRESH_BINARY | cv2.THRESH_OTSU,
        }
        cv_method = method_map.get(method, cv2.THRESH_BINARY)
        _, result = cv2.threshold(img, thresh, max_val, cv_method)
        return result

    @staticmethod
    def adaptive_threshold(img: np.ndarray, block_size: int = 11, c: int = 2,
                           method: str = 'mean') -> np.ndarray:
        if len(img.shape) == 3:
            img = ImageUtils.bgr_to_gray(img)
        method_map = {
            'mean': cv2.ADAPTIVE_THRESH_MEAN_C,
            'gaussian': cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        }
        cv_method = method_map.get(method, cv2.ADAPTIVE_THRESH_MEAN_C)
        return cv2.adaptiveThreshold(img, 255, cv_method, cv2.THRESH_BINARY, block_size, c)

    @staticmethod
    def color_mask(img: np.ndarray, lower: tuple[int, int, int],
                   upper: tuple[int, int, int], color_space: str = 'hsv') -> np.ndarray:
        """Create a binary mask for pixels within a color range."""
        if color_space == 'hsv':
            converted = ImageUtils.bgr_to_hsv(img)
        elif color_space == 'rgb':
            converted = ImageUtils.bgr_to_rgb(img)
        else:
            converted = img
        lower_arr = np.array(lower, dtype=np.uint8)
        upper_arr = np.array(upper, dtype=np.uint8)
        return cv2.inRange(converted, lower_arr, upper_arr)

    @staticmethod
    def extract_connected_components(mask: np.ndarray,
                                     min_area: int = 100) -> list[dict]:
        """Extract connected components from a binary mask.

        Returns:
            List of {x, y, w, h, area, center_x, center_y} dicts.
        """
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        components = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < min_area:
                continue
            x = stats[i, cv2.CC_STAT_LEFT]
            y = stats[i, cv2.CC_STAT_TOP]
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            components.append({
                'x': int(x), 'y': int(y), 'w': int(w), 'h': int(h),
                'area': int(area),
                'center_x': int(x + w / 2),
                'center_y': int(y + h / 2),
            })
        return components

    @staticmethod
    def compute_ssim(img1: np.ndarray, img2: np.ndarray) -> float:
        """Compute Structural Similarity Index between two images.

        Both images should be grayscale and same size.
        Returns value in [-1, 1], where 1 = identical.
        """
        if len(img1.shape) == 3:
            img1 = ImageUtils.bgr_to_gray(img1)
        if len(img2.shape) == 3:
            img2 = ImageUtils.bgr_to_gray(img2)

        if img1.shape != img2.shape:
            img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

        img1 = img1.astype(np.float64)
        img2 = img2.astype(np.float64)

        C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
        mu1, mu2 = img1.mean(), img2.mean()
        sigma1_sq = img1.var()
        sigma2_sq = img2.var()
        sigma12 = ((img1 - mu1) * (img2 - mu2)).mean()

        numerator = (2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)
        denominator = (mu1 ** 2 + mu2 ** 2 + C1) * (sigma1_sq + sigma2_sq + C2)
        return numerator / denominator

    @staticmethod
    def pixel_diff(img1: np.ndarray, img2: np.ndarray,
                   normalize: bool = True) -> float:
        """Compute pixel-level difference between two images.

        Returns:
            If normalize=True: fraction of pixels that differ (0-1).
            If normalize=False: raw count of differing pixels.
        """
        if img1.shape != img2.shape:
            img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
        if len(img1.shape) == 3:
            diff = np.sum(np.abs(img1.astype(np.int16) - img2.astype(np.int16)), axis=2)
            threshold = 30 * 3  # per-channel threshold
        else:
            diff = np.abs(img1.astype(np.int16) - img2.astype(np.int16))
            threshold = 30

        changed = np.sum(diff > threshold)
        total = diff.size
        return changed / total if normalize else float(changed)

    @staticmethod
    def crop_region(img: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
        """Crop a region from an image with bounds checking."""
        img_h, img_w = img.shape[:2]
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(img_w, x + w)
        y2 = min(img_h, y + h)
        return img[y1:y2, x1:x2]

    @staticmethod
    def resize_to_target(img: np.ndarray, target_w: int, target_h: int,
                         keep_aspect: bool = True) -> np.ndarray:
        """Resize image to target dimensions."""
        if keep_aspect:
            h, w = img.shape[:2]
            scale = min(target_w / w, target_h / h)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = cv2.resize(img, (new_w, new_h))
            # Pad to target
            if new_w < target_w or new_h < target_h:
                pad_top = (target_h - new_h) // 2
                pad_bottom = target_h - new_h - pad_top
                pad_left = (target_w - new_w) // 2
                pad_right = target_w - new_w - pad_left
                resized = cv2.copyMakeBorder(
                    resized, pad_top, pad_bottom, pad_left, pad_right,
                    cv2.BORDER_CONSTANT, value=[0, 0, 0]
                )
            return resized
        return cv2.resize(img, (target_w, target_h))
