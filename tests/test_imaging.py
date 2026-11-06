"""Frame differencing on the scripted scenes, with no camera and no ffmpeg."""
from hermes_camera_watch import imaging, scenes


def test_png_roundtrip_and_package_is_visible():
    rgb = scenes.porch(True)
    png = imaging.encode_png(scenes.WIDTH, scenes.HEIGHT, rgb)
    width, height, decoded = imaging.decode_png(png)
    assert (width, height) == (scenes.WIDTH, scenes.HEIGHT)
    assert decoded == rgb
    empty = scenes.pixel(scenes.porch(False), *scenes.PROBE)
    packed = scenes.pixel(rgb, *scenes.PROBE)
    assert empty != packed


def test_package_crosses_the_motion_threshold_and_quiet_does_not():
    def gray(scene, step):
        frame, _caption, _event = scenes.render(scene, step)
        return imaging.to_gray_small(frame, scenes.WIDTH, scenes.HEIGHT)

    score = imaging.changed_fraction(gray("package", 0), gray("package", 1), 18)
    assert score > 0.08
    assert imaging.changed_fraction(gray("quiet", 0), gray("quiet", 3), 18) == 0.0
    assert imaging.changed_fraction(gray("package", 1), gray("package", 4), 18) == 0.0
