import cv2
import numpy as np


def test_image_ink_stats_detects_nonblank_image(tmp_path):
    from winpilot.perception.vision_cv import image_ink_stats

    path = tmp_path / "drawing.png"
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 30), (80, 90), (0, 0, 0), 3)
    cv2.imwrite(str(path), image)

    stats = image_ink_stats(str(path))

    assert stats["exists"]
    assert stats["readable"]
    assert not stats["blankish"]
    assert stats["ink_ratio"] > 0
    assert stats["component_count"] >= 1


def test_image_ink_stats_detects_blankish_image(tmp_path):
    from winpilot.perception.vision_cv import image_ink_stats

    path = tmp_path / "blank.png"
    image = np.full((80, 100, 3), 255, dtype=np.uint8)
    cv2.imwrite(str(path), image)

    stats = image_ink_stats(str(path))

    assert stats["exists"]
    assert stats["readable"]
    assert stats["blankish"]
    assert stats["component_count"] == 0


def test_image_color_stats_detects_requested_colors(tmp_path):
    from winpilot.perception.vision_cv import image_color_stats

    path = tmp_path / "colors.png"
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.line(image, (10, 20), (140, 20), (0, 0, 255), 5)
    cv2.line(image, (10, 60), (140, 60), (255, 0, 0), 5)
    cv2.imwrite(str(path), image)

    stats = image_color_stats(str(path))

    assert stats["colors"]["red"]["visible"]
    assert stats["colors"]["blue"]["visible"]
    assert not stats["colors"]["green"]["visible"]


def test_color_presence_reports_largest_color_component():
    from winpilot.perception.vision_cv import color_presence

    image = np.full((140, 180, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (10, 10), (20, 20), (255, 0, 0), -1)
    cv2.rectangle(image, (80, 50), (150, 110), (255, 0, 0), -1)

    stats = color_presence(image)

    assert stats["colors"]["blue"]["visible"]
    assert stats["colors"]["blue"]["largest_bbox"] == [80, 50, 151, 111]


def test_likely_canvas_bbox_prefers_large_light_surface():
    from winpilot.perception.vision_cv import likely_canvas_bbox

    image = np.full((220, 300, 3), 30, dtype=np.uint8)
    image[70:200, 40:280] = 255
    cv2.rectangle(image, (230, 20), (250, 40), (0, 0, 255), -1)

    assert likely_canvas_bbox(image) == [40, 70, 280, 200]
