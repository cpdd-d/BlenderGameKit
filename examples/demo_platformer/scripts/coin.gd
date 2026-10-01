extends Area2D
## 金币:被玩家碰到就加分并消失。
##
## 分数存在玩家身上(由节点图生成的 player.gd 持有 `score` 变量)。
## 这里用 group 找玩家，而不是把 Player 路径写死 —— 场景里放几份金币
## 都不用改代码。

@export var score_value: int = 10
@export var spin_speed: float = 3.0

var _taken := false


func _ready() -> void:
	body_entered.connect(_on_body_entered)


func _process(delta: float) -> void:
	# 轻轻上下浮动，让金币看起来"活着"
	rotation += spin_speed * delta


func _on_body_entered(body: Node) -> void:
	if _taken:
		return
	if not body.is_in_group("player"):
		return
	_taken = true
	if body.has_method("add_score"):
		body.add_score(score_value)
	queue_free()
