import subprocess

message = """fix: 相机解析不再用嵌套三元 —— 弹簧臂终于能拿到相机了

用户第 28 轮给了 Godot 调试器的完整报错,一眼定位:

    game_logic.gd:130/134/138/141
    INCOMPATIBLE_TERNARY: Values of the ternary operator are not
    mutually compatible.

生成的相机表达式是一个**嵌套三元**:

    _gk_spring_cam = (_gk_cam_auto if _gk_cam_auto != null else
        (get_viewport().get_camera_3d() if ... != null else
         (get_viewport().get_camera_2d() if ... else
          get_tree().get_first_node_in_group("gk_camera"))))

三个分支的类型是 Camera3D / Camera2D / Node —— 凑不到一起,Godot 拒绝,
**那一行算不出可用相机** -> 弹簧臂每帧走"没找到相机"分支 -> 完全不生效。
这也解释了"切视角没反应、镜头怪":相机根本没被弹簧臂接管。

改成类型统一的辅助函数 `_gk_resolve_camera(self) -> Node`,内部全是普通 if:
    ①视口里正在用的 ②gk_camera 分组 ③场景里第一台(顺手 make_current)

过程中的坑:
  * 同名函数有两份(`_camera_node_expr` 定义了两次),Python 用最后一个,
    于是"改好的那份"被旧定义覆盖 —— 又把 5 个被误删的处理器从 git 捞回来
  * 顺手清掉 Godot 的 UNUSED_PARAMETER 噪声(弹簧臂 basis 按需声明)

验证:299 离线 · 19/19 真 Blender · 22/22 动画示例 · 20/20 2.5D ·
      21/21 关卡 · 冒烟 59/59 · ANIM_VERIFY=OK
"""

subprocess.run(["git", "add", "-A"], cwd=r"D:\BlenderGameKit", check=False)
result = subprocess.run(
    ["git", "-c", "core.quotepath=false", "commit", "-q", "-m", message],
    cwd=r"D:\BlenderGameKit", capture_output=True)
print("returncode:", result.returncode)
log = subprocess.run(["git", "log", "--oneline", "-2"], cwd=r"D:\BlenderGameKit",
                     capture_output=True)
print(log.stdout.decode("utf-8", errors="replace"))
