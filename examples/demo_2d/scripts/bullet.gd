extends Node2D
# 子弹自己往前飞,1 秒后消失 —— 演示『实例_生成』出来的东西真的会动
var life := 0.0
var speed := 520.0

func _process(delta: float) -> void:
	life += delta
	position.x += speed * delta
	if life > 1.0:
		queue_free()
