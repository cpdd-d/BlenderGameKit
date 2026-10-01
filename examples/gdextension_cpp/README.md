# GDExtension C++ 骨架

在 Godot 里用 C++ 写逻辑的**最小可编译例子**。

## 这个例子做什么

`GKCharacter` 继承 `CharacterBody3D`,用 C++ 实现平台跳跃角色的移动
(重力、按方向移动、跳跃、击退),并把参数暴露给 GDScript / 场景文件。

## 怎么用

### 1. 准备 godot-cpp

版本必须和你的 Godot 对应(Godot 4.4 → 分支 `4.4`):

```powershell
git clone -b 4.4 https://github.com/godotengine/godot-cpp ..\godot-cpp
```

没装 git 就去 GitHub 下载 zip 解压到 `examples\godot-cpp\`。

### 2. 编译

```powershell
# 用 SCons(官方推荐)
pip install scons
powershell -ExecutionPolicy Bypass -File build.ps1

# 或者用 CMake(你机器上有 D:\cmake 和 VS2026)
powershell -ExecutionPolicy Bypass -File build.ps1 -UseCMake
```

产物会出现在 `bin/` 下,文件名必须和 `gk_demo.gdextension` 里写的一致。

### 3. 在 Godot 里用

把 `gk_demo.gdextension`(以及 `bin/`)放进你的 Godot 工程,**重启编辑器**。
然后在场景里:

```
Character (GKCharacter)        ← 直接把这个类当节点用
├── Model (instance of .glb)
└── Collision (CollisionShape3D)
```

GDScript 里:

```gdscript
extends Node3D

@onready var hero: GKCharacter = $Character

func _physics_process(_delta: float) -> void:
	var dir := Input.get_vector("move_left", "move_right", "move_up", "move_down")
	hero.move_by_input(Vector3(dir.x, 0.0, dir.y))
	hero.jump_pressed = Input.is_action_just_pressed("jump")
	if hero.get_is_grounded():
		pass  # 在地面
```

## 和节点图怎么配合

推荐分工:**C++ 写每帧的热点计算,节点图/GDScript 写游戏规则**。

| 谁 | 负责 |
|---|---|
| `GKCharacter`(C++) | 重力、碰撞移动、击退这些每帧都跑的 |
| 节点图导出的 GDScript | 输入判定、状态切换、动画、受伤、死亡 |
| Godot 场景 (.tscn) | 把 C++ 节点、模型、碰撞体装配起来 |

## 需要注意的坑

1. **改完 C++ 必须重启 Godot 编辑器**,热重载不可靠。
2. **`.gdextension` 里的 `entry_symbol` 必须和 `register_types.cpp` 里的函数名一致**
   (这里是 `gk_library_init`)。
3. **godot-cpp 版本和 Godot 版本差太多会在链接期报一堆符号错误**,不是你的代码问题。
4. **GDExtension 不能直接暴露成员变量**,必须写 `set_xxx`/`get_xxx` 再用
   `ADD_PROPERTY` 绑定 —— 这是最容易忘的一步,忘了的话 GDScript 里读不到属性。
5. `_bind_methods()` 里 `ClassDB::bind_method` 的方法名要和 GDScript 里调用的一致,
   写错了只会在运行时静默失败。

## 什么时候不该用 C++

如果只是"角色移动 + 状态机",GDScript 完全够用,而且改一行就能测。
**先用 GDScript 写出来,profile 确认是热点,再搬进 C++。**
