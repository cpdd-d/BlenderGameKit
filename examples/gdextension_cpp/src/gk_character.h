#ifndef GK_CHARACTER_H
#define GK_CHARACTER_H

#include <godot_cpp/classes/character_body3d.hpp>
#include <godot_cpp/classes/input.hpp>
#include <godot_cpp/variant/vector3.hpp>

namespace godot {

// C++ 版的角色控制器。
// GDScript 里可以这样用:
//     $Character.speed = 240.0
//     $Character.move_by_input(dir)
//     if $Character.get_is_grounded(): ...
class GKCharacter : public CharacterBody3D {
	GDCLASS(GKCharacter, CharacterBody3D)

private:
	Vector3 direction;
	double speed = 260.0;
	double jump_velocity = 520.0;
	double gravity_scale = 1.0;
	bool jump_pressed = false;

protected:
	static void _bind_methods();

public:
	GKCharacter();
	~GKCharacter();

	void _physics_process(double p_delta) override;

	// 给 GDScript 调用的接口
	void move_by_input(const Vector3 &p_direction);
	void apply_knockback(const Vector3 &p_impulse);
	bool get_is_grounded() const;

	// 属性访问器(必须显式写，GDExtension 不支持直接暴露成员变量)
	void set_speed(double p_value);
	double get_speed() const;
	void set_jump_velocity(double p_value);
	double get_jump_velocity() const;
	void set_gravity_scale(double p_value);
	double get_gravity_scale() const;
	void set_jump_pressed(bool p_value);
};

} // namespace godot

#endif // GK_CHARACTER_H
