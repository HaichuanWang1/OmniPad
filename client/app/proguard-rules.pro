# R8 规则。
#
# 本项目客户端没有任何反射查找（无 Class.forName / getDeclaredMethod / ::class.java），
# 所以不需要为「按名字找类」保留什么；清单里声明的 Activity 由 AGP 的默认规则保留，
# Compose 与协程自带 consumer 规则。
#
# 唯一要补的是崩溃堆栈的可读性：默认会把行号一起丢掉，release 崩溃时只剩混淆后的
# 名字，对着 mapping.txt 也还原不出位置。

# 保留行号，便于用 mapping.txt 还原 release 崩溃堆栈
-keepattributes SourceFile,LineNumberTable

# 把原始源文件名替换成固定串，避免泄露源码文件名，同时不影响行号还原
-renamesourcefileattribute SourceFile
