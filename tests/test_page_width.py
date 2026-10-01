"""Every page fits the window it is shown in.

At 1536 x 1000 (a 1920-pixel screen at 125 % scaling) each page's minimum width stays within the page area, in
both languages.  The fault page needed 1,918 px and its results were cut off on the left: a combo box kept the
minimum width of its longest item (Qt caches it across a size-policy change), every plot panel held 636 px (six
push buttons of 80 px), a one-line check-box label and the editor's explanation lines did not wrap, and the
campaign side panel took its full content width.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from traction_workbench.i18n import language, set_language  # noqa: E402


@pytest.fixture(scope="module")
def app():
    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    a.setProperty("twb_selftest", True)
    yield a


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_every_page_fits_a_1536_px_window(app, lang):
    from traction_workbench.desktop.main_window import MainWindow
    before = language()
    set_language(lang)
    try:
        w = MainWindow()
        w.resize(1536, 1000)
        w.show()
        app.processEvents()
        over = {}
        for key, pg in w.pages.items():
            w.show_page(key)
            app.processEvents()
            room, need = pg.parentWidget().width(), pg.minimumSizeHint().width()
            if need > room:
                over[key] = (need, room)
        w.close()
    finally:
        set_language(before)
    assert not over, over


def test_a_combo_box_shrinks_after_the_policy_change(app):
    """``tidy_inputs`` lets a combo box shrink with its column: the size Qt computed under the old policy is dropped."""
    from PySide6.QtWidgets import QComboBox, QVBoxLayout, QWidget

    from traction_workbench.desktop.widgets import tidy_inputs
    root = QWidget()
    cb = QComboBox()
    cb.addItems(["a scenario title long enough to hold a whole input panel open " * 3, "b"])
    QVBoxLayout(root).addWidget(cb)
    wide = cb.minimumSizeHint().width()                  # computed (and cached) under the default policy
    tidy_inputs(root)
    assert cb.minimumSizeHint().width() < wide / 3
    assert cb.toolTip() == cb.currentText()
