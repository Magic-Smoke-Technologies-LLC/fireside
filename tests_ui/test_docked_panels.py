"""The panels are docked, and the rail is a rail.

Direction A promised docked panels instead of sheets floating in a dark room,
and a CSS claim is worth checking in a real browser: a sheet meant to sit on the
right edge but floating 50px short is still a card, and no markup test sees it.

So this measures boxes, at two widths, and asks one thing of each: wide screen,
the sheet touches the right edge and the full height; phone, it comes up from
the bottom edge; Account, the sections sit in a narrow rail to the left of the
panel rather than a second content column.
"""

import unittest

from tests_ui.support import BrowserTest, wait_visible


class TestDockedPanels(BrowserTest):

    def box(self, sel):
        return self.page.eval_on_selector(sel, "e => { const r = e.getBoundingClientRect();"
            " return {x:r.x, y:r.y, w:r.width, h:r.height, right:r.right, bottom:r.bottom}; }")

    def test_panels_and_rail(self):
        self.register("dock_geo")
        self.wait_in_app()
        self.page.click('#tabs button[data-v="set"]')
        wait_visible(self.page, "#settabs")
        self.page.click('#settabs button[data-p="account"]')
        self.page.wait_for_timeout(300)
        # account: a rail on the left, the panel to its right, same row
        nav = self.box("#settabs")
        panel = self.box('#v-set .setpanel.on')
        self.assertLess(nav["right"], panel["x"], "the rail has to sit left of the panel")
        self.assertLess(nav["w"], 240, "rail is a rail, not a second content column")
        self.assertGreater(panel["w"], 500, "the panel keeps the page width")
        # a docked sheet: full height on the right edge of a wide screen
        self.page.click('#tabs button[data-v="lib"]')
        self.page.click("#addbtn")
        wait_visible(self.page, "#exbtn")
        self.page.click("#exbtn")
        wait_visible(self.page, "#settingsbtn", 30000)
        self.page.click("#settingsbtn")
        self.page.wait_for_timeout(400)
        sheet = self.box("#settingsdlg .sheet")
        vw = self.page.evaluate("() => innerWidth")
        vh = self.page.evaluate("() => innerHeight")
        self.assertAlmostEqual(sheet["right"], vw, delta=1, msg="docked to the right edge")
        self.assertAlmostEqual(sheet["h"], vh, delta=1, msg="docked full height")
        self.assertAlmostEqual(sheet["x"], vw - sheet["w"], delta=1)
        # phone: the same sheet comes up from the bottom edge
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_timeout(400)
        sheet = self.box("#settingsdlg .sheet")
        self.assertAlmostEqual(sheet["bottom"], 844, delta=1, msg="docked to the bottom edge")
        self.assertLess(sheet["x"], 2)
        self.assert_no_page_errors()


if __name__ == "__main__":
    unittest.main()

