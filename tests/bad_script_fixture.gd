extends CharacterBody3D

func _physics_process(_delta: float) -> void:
    var anim_state := "idle"
    if anim_state == "idle" and:
        print("这行是故意写错的")
