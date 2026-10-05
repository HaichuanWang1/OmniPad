"""tray.py 与图标文件的测试。

托盘图标本身需要真实的任务栏（CI 的 Windows runner 没有），所以**真**创建图标的
用例默认跳过，用 OMNIPAD_TRAY_TEST=1 打开。默认跑的是那些真正会出错的部分：

  * Win32 结构体尺寸（cbSize 错一个字节 Shell_NotifyIcon 就静默失败）
  * ctypes 原型声明（不声明的话 64 位 HWND 会被截断成垃圾指针，不报错）
  * 菜单 ID 的低 16 位解析
  * 图标文件本身是不是合法的 ICO

运行：
    cd server && python test_tray.py
    $env:OMNIPAD_TRAY_TEST=1; python test_tray.py    # 含真实托盘创建
"""
import ctypes
import os
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tray

IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8
RUN_LIVE = os.environ.get("OMNIPAD_TRAY_TEST") == "1"


class StructLayoutTest(unittest.TestCase):
    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_notify_icon_data_size(self):
        """cbSize 必须等于结构体自身大小。

        多一个字节或少一个字节，Shell_NotifyIcon 都只是返回 FALSE ——
        没有异常、没有日志，就是图标不出现。
        """
        expected = 976 if IS_64BIT else 956
        self.assertEqual(ctypes.sizeof(tray.NOTIFYICONDATAW), expected)

    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_notify_icon_data_field_offsets(self):
        data = tray.NOTIFYICONDATAW()
        self.assertEqual(tray.NOTIFYICONDATAW.cbSize.offset, 0)
        self.assertEqual(tray.NOTIFYICONDATAW.hWnd.offset, 8 if IS_64BIT else 4)
        self.assertEqual(tray.NOTIFYICONDATAW.hIcon.offset, 32 if IS_64BIT else 24)
        self.assertEqual(tray.NOTIFYICONDATAW.szTip.offset, 40 if IS_64BIT else 28)

    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_tooltip_field_holds_127_characters(self):
        self.assertEqual(tray.NOTIFYICONDATAW.szTip.size, 128 * ctypes.sizeof(
            ctypes.c_wchar))

    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_callback_message_is_in_the_app_range(self):
        # WM_APP..0xBFFF 是留给应用的消息区间，用系统消息号会打架
        self.assertGreaterEqual(tray.WM_TRAYICON, 0x8000)
        self.assertLessEqual(tray.WM_TRAYICON, 0xBFFF)


class PrototypeTest(unittest.TestCase):
    """不声明原型的后果是静默的：64 位返回值被截成 int。"""

    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_handle_returning_functions_declare_their_restype(self):
        from ctypes import wintypes

        expected = {
            "CreateWindowExW": wintypes.HWND,
            "CreatePopupMenu": wintypes.HMENU,
            "LoadImageW": wintypes.HANDLE,
        }
        for name, restype in expected.items():
            with self.subTest(function=name):
                self.assertIs(getattr(tray.user32, name).restype, restype)

    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_window_proc_returns_a_pointer_sized_value(self):
        self.assertEqual(
            ctypes.sizeof(tray.user32.DefWindowProcW.restype),
            ctypes.sizeof(ctypes.c_void_p),
        )

    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_shell_notify_icon_returns_a_bool(self):
        from ctypes import wintypes
        self.assertIs(tray.shell32.Shell_NotifyIconW.restype, wintypes.BOOL)

    @unittest.skipUnless(tray.IS_WINDOWS, "仅 Windows")
    def test_string_arguments_are_declared_as_wide(self):
        from ctypes import wintypes
        argtypes = tray.user32.CreateWindowExW.argtypes
        self.assertIn(wintypes.LPCWSTR, argtypes)


class LowordTest(unittest.TestCase):
    def test_extracts_the_menu_id(self):
        self.assertEqual(tray.loword(3), 3)
        self.assertEqual(tray.loword(3 | (0x1234 << 16)), 3)
        self.assertEqual(tray.loword(0xFFFF), 0xFFFF)

    def test_menu_ids_are_16_bit(self):
        self.assertEqual(tray.loword(0x1_0001), 1)


class TooltipTest(unittest.TestCase):
    def test_newlines_are_flattened(self):
        self.assertEqual(tray.truncate_tooltip("a\nb"), "a b")

    def test_strips(self):
        self.assertEqual(tray.truncate_tooltip("  hi  "), "hi")

    def test_short_text_is_untouched(self):
        self.assertEqual(tray.truncate_tooltip("OmniPad"), "OmniPad")

    def test_long_text_is_truncated_within_the_limit(self):
        text = tray.truncate_tooltip("x" * 500)
        self.assertLessEqual(len(text), tray.TOOLTIP_LIMIT)
        self.assertTrue(text.endswith("…"))

    def test_none_is_safe(self):
        self.assertEqual(tray.truncate_tooltip(None), "")


class IconFileTest(unittest.TestCase):
    """图标是脚本生成的，格式对不对得有人盯着 —— 破的 ico 只会让图标不出现。"""

    def setUp(self):
        self.path = tray.default_icon_path()
        with open(self.path, "rb") as f:
            self.data = f.read()

    def test_file_exists_where_the_tray_looks_for_it(self):
        self.assertTrue(os.path.exists(self.path), f"缺少图标文件 {self.path}")

    def test_header(self):
        reserved, kind, count = struct.unpack_from("<HHH", self.data, 0)
        self.assertEqual((reserved, kind), (0, 1), "不是合法的 ICO 文件头")
        self.assertGreaterEqual(count, 3, "至少要有 16/32/48 三个尺寸")

    def test_every_entry_is_a_well_formed_32bit_bitmap(self):
        _reserved, _kind, count = struct.unpack_from("<HHH", self.data, 0)
        seen = set()
        for index in range(count):
            with self.subTest(index=index):
                width, height, _colors, _res, planes, bits, size, offset = \
                    struct.unpack_from("<BBBBHHII", self.data, 6 + 16 * index)
                self.assertEqual(planes, 1)
                self.assertEqual(bits, 32, "必须是 32 位带 alpha，否则边缘是硬锯齿")
                self.assertLessEqual(offset + size, len(self.data))
                seen.add(width)

                bi_size, bi_width, bi_height = struct.unpack_from("<Iii", self.data, offset)
                self.assertEqual(bi_size, 40)
                self.assertEqual(bi_width, width)
                self.assertEqual(bi_height, height * 2, "高度要算上 AND 掩码")

        self.assertIn(16, seen, "任务栏小图标用的是 16px")
        self.assertIn(32, seen, "exe 图标默认取 32px")

    def test_contains_no_black_halo(self):
        """降采样时如果不按 alpha 预乘，半透明边缘会渗出黑色。"""
        _reserved, _kind, count = struct.unpack_from("<HHH", self.data, 0)
        for index in range(count):
            width, height, _c, _r, _p, _b, size, offset = \
                struct.unpack_from("<BBBBHHII", self.data, 6 + 16 * index)
            if width != 32:
                continue
            body = self.data[offset + 40:offset + 40 + width * height * 4]
            for i in range(0, len(body), 4):
                b, g, r, a = body[i:i + 4]
                if a == 0:
                    continue
                with self.subTest(pixel=i // 4):
                    self.assertGreater(
                        max(r, g, b), 20,
                        "不透明像素不该是接近纯黑的颜色（边缘预乘算错了）",
                    )
            break


class StartWithoutIconTest(unittest.TestCase):
    def test_missing_icon_file_is_not_fatal(self):
        icon = tray.TrayIcon(
            icon_path=os.path.join(os.path.dirname(__file__), "nope.ico"),
            tooltip="x", menu_items=[], on_command=lambda _id: None,
        )
        self.assertFalse(icon.start(), "图标文件缺失时应当返回 False，而不是抛异常")

    @unittest.skipIf(tray.IS_WINDOWS, "非 Windows 上应当直接拒绝")
    def test_non_windows_is_refused(self):
        icon = tray.TrayIcon(
            icon_path=tray.default_icon_path(), tooltip="x",
            menu_items=[], on_command=lambda _id: None,
        )
        self.assertFalse(icon.start())

    def test_stop_without_start_is_safe(self):
        tray.TrayIcon(
            icon_path=tray.default_icon_path(), tooltip="x",
            menu_items=[], on_command=lambda _id: None,
        ).stop()


@unittest.skipUnless(tray.IS_WINDOWS and RUN_LIVE,
                     "需要真实任务栏；用 OMNIPAD_TRAY_TEST=1 打开")
class LiveTrayTest(unittest.TestCase):
    """真的把图标放进通知区域。CI 上没有任务栏，所以默认跳过。"""

    def test_icon_can_be_created_and_removed(self):
        commands = []
        icon = tray.TrayIcon(
            icon_path=tray.default_icon_path(),
            tooltip="OmniPad 测试",
            menu_items=[(1, "显示"), (2, None), (3, "退出")],
            on_command=commands.append,
        )
        try:
            self.assertTrue(icon.start(), "托盘图标创建失败")
            time.sleep(0.5)
            icon.update_tooltip("OmniPad 更新后的提示")
            time.sleep(0.5)
        finally:
            icon.stop()
        self.assertFalse(icon._started and icon._hwnd, "stop 之后窗口句柄应当被释放")


if __name__ == "__main__":
    unittest.main(verbosity=2)
