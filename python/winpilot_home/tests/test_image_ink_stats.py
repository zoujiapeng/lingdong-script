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


def test_likely_canvas_bbox_excludes_vertical_overlay_on_work_surface():
    from winpilot.perception.vision_cv import likely_canvas_bbox

    image = np.full((260, 420, 3), 30, dtype=np.uint8)
    image[60:240, 35:400] = 255
    # A dark side control sits over the document. The white component remains
    # connected around it, but it is not a safe place for a drawing stroke.
    image[70:230, 35:82] = 20

    assert likely_canvas_bbox(image) == [82, 60, 400, 240]


def test_likely_canvas_bbox_does_not_shrink_after_closed_ink_is_added():
    from winpilot.perception.vision_cv import likely_canvas_bbox

    image = np.full((720, 1008, 3), 30, dtype=np.uint8)
    image[258:671, 33:1006] = 255
    # Keep the same dark side control used by a real editable surface.
    image[258:671, 33:126] = 20
    # Closed marks split their white interiors into separate connected
    # components. They are content, not new surface boundaries.
    for center, axes in (
        ((310, 350), (60, 45)),
        ((390, 350), (60, 45)),
        ((470, 330), (55, 50)),
        ((550, 340), (55, 50)),
        ((610, 355), (35, 25)),
        ((280, 320), (35, 35)),
        ((350, 290), (30, 30)),
    ):
        cv2.ellipse(image, center, axes, 0, 0, 360, (0, 0, 0), 4)
    cv2.line(image, (350, 380), (480, 650), (0, 0, 0), 4)
    cv2.line(image, (370, 380), (500, 650), (0, 0, 0), 4)

    assert likely_canvas_bbox(image) == [126, 258, 1006, 671]


def test_credible_light_work_surface_rejects_ambiguous_panels():
    from winpilot.perception.vision_cv import credible_light_work_surface

    image = np.full((300, 600, 3), 50, dtype=np.uint8)
    image[60:260, 30:280] = 255
    image[60:260, 320:570] = 255

    result = credible_light_work_surface(image)

    assert result["ok"] is False
    assert "multiple similarly plausible" in result["reason"]


def test_credible_light_work_surface_rejects_nested_light_modal():
    from winpilot.perception.vision_cv import credible_light_work_surface

    image = np.full((500, 1000, 3), 35, dtype=np.uint8)
    image[50:450, 50:950] = 255
    # Large light dialog, disconnected from the document by a dark border.
    image[150:350, 150:850] = 20
    image[155:345, 155:845] = 248

    result = credible_light_work_surface(image)

    assert result["ok"] is False
    assert "nested light panel or modal" in result["reason"]
