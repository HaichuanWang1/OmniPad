"""为 exe 生成 Windows 版本资源（PyInstaller 的 --version-file）。

    python scripts/make_version_info.py 1.0.0-beta1.9 out.txt

生成之后 exe 的「属性 → 详细信息」里才有产品名、版本号、版权 —— 否则那里
是一片空白，用户看到的是一个来路不明的可执行文件，SmartScreen 的警告就更吓人。

版本号的唯一来源仍是仓库根目录的 VERSION；数字部分（filevers/prodvers）供
Windows 做大小比较，完整字符串（含 -beta1.9）供人看。
"""
import sys


def numeric_parts(version):
    """`1.0.0-beta1.9` → `(1, 0, 0, 0)`。

    `filevers` 只能是四个 0..65535 的整数，预发布后缀没法表达 —— 它去
    FileVersion 字符串里待着。
    """
    core = (version or "").split("-")[0]
    parts = []
    for chunk in core.split("."):
        try:
            parts.append(max(0, min(65535, int(chunk))))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={prodvers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'OmniPad'),
        StringStruct('FileDescription', 'OmniPad 服务端'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'OmniPad-Server'),
        StringStruct('LegalCopyright', 'Apache License 2.0'),
        StringStruct('OriginalFilename', 'OmniPad-Server.exe'),
        StringStruct('ProductName', 'OmniPad'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def build(version):
    parts = numeric_parts(version)
    return TEMPLATE.format(
        version=version,
        filevers=parts,
        prodvers=parts,
    )


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    version, output = argv[1], argv[2]
    with open(output, "w", encoding="utf-8") as f:
        f.write(build(version))
    print(f"已写入版本资源 {output}（{version}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
