import subprocess

message = """fix: 弹簧臂与"切相机"抢同一台相机 —— C/V 改成调杆长

用户第 27 轮:"还是摄像机问题",并给了 Godot 调试器截图 + 诊断报告。

诊断报告证明 Blender 侧数据全对(插件 132 节点、角色网格 1x1x1.6、
碰撞 auto、手填尺寸全 0、主相机=侧视机位、65 节点/65 连线)。
问题在运行时:弹簧臂每帧写相机位置,而『相机_切换』也在写同一台相机
—— 切相机的过渡永远被弹簧臂覆盖,表现就是"切了没反应/闪一下/镜头怪"。

改法:弹簧臂是这台相机的主人,C/V 改成改它的参数:
    C = 拉远到 14 米、俯仰 55 度(全局视角)
    V = 收回 5 米、俯仰 12 度(第三人称)
两者共用"相机跟着角色 + 射线防穿模",不再互相覆盖。

顺带
  * 弹簧臂的 _gk_spring_basis 改成"朝向平滑时才声明"(Godot 调试器
    会警告 declared but never used)
  * 关卡开场的『相机_设置』只设视野,机位交给弹簧臂
"""

subprocess.run(["git", "add", "-A"], cwd=r"D:\BlenderGameKit", check=False)
result = subprocess.run(
    ["git", "-c", "core.quotepath=false", "commit", "-q", "-m", message],
    cwd=r"D:\BlenderGameKit", capture_output=True, text=True)
print("returncode:", result.returncode)
print(result.stdout[:400])
print(result.stderr[:400])
log = subprocess.run(["git", "log", "--oneline", "-2"], cwd=r"D:\BlenderGameKit",
                     capture_output=True, text=True)
print(log.stdout)
