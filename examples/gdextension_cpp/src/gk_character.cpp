#include "gk_character.h"

#include <godot_cpp/core/class_db.hpp>
#include <godot_cpp/variant/utility_functions.hpp>

using namespace godot;

// ============================================================================
// GKCharacter -- 把"每帧都要跑的移动逻辑"放到 C++ 里
//
// 为什么值得用 C++:节点图导出的 GDScript 适合写规则和状态，
// 但如果有几百上千个单位每帧都要算移动/避障，GDScript 会成为瓶颈。
// 这个类演示了三件事:
//   1. 怎么继承引擎节点类(CharacterBody3D)
//   2. 怎么把属性暴露给 GDScript / 场景文件(set/get + ClassDB 绑定)
//   3. 怎么把方法暴露给 GDScript 调用
// ============================================================================

GKCharacter::GKCharacter() {
}

GKCharacter::~GKCharacter() {
}

void GKCharacter::_bind_methods() {
	// 暴露方法给 GDScript:  $Character.move_by_input(dir)
	ClassDB::bind_method(D_METHOD("move_by_input", "direction"), &GKCharacter::move_by_input);
	ClassDB::bind_method(D_METHOD("apply_knockback", "impulse"), &GKCharacter::apply_knockback);
	ClassDB::bind_method(D_METHOD("get_is_grounded"), &GKCharacter::get_is_grounded);

	// 暴露属性。GDScript 和 .tscn 里都能直接写 speed = 260.0
	ClassDB::bind_method(D_METHOD("set_speed", "value"), &GKCharacter::set_speed);
	ClassDB::bind_method(D_METHOD("get_speed"), &GKCharacter::get_speed);
	ADD_PROPERTY(PropertyInfo(Variant::FLOAT, "speed", PROPERTY_HINT_RANGE, "0,2000,1"),
			"set_speed", "get_speed");

	ClassDB::bind_method(D_METHOD("set_jump_velocity", "value"), &GKCharacter::set_jump_velocity);
	ClassDB::bind_method(D_METHOD("get_jump_velocity"), &GKCharacter::get_jump_velocity);
	ADD_PROPERTY(PropertyInfo(Variant::FLOAT, "jump_velocity", PROPERTY_HINT_RANGE, "0,3000,1"),
			"set_jump_velocity", "get_jump_velocity");

	ClassDB::bind_method(D_METHOD("set_gravity_scale", "value"), &GKCharacter::set_gravity_scale);
	ClassDB::bind_method(D_METHOD("get_gravity_scale"), &GKCharacter::get_gravity_scale);
	ADD_PROPERTY(PropertyInfo(Variant::FLOAT, "gravity_scale", PROPERTY_HINT_RANGE, "0,5,0.01"),
			"set_gravity_scale", "get_gravity_scale");

	ClassDB::bind_method(D_METHOD("set_jump_pressed", "value"), &GKCharacter::set_jump_pressed);
	ADD_PROPERTY(PropertyInfo(Variant::BOOL, "jump_pressed"), "", "set_jump_pressed");
}

void GKCharacter::_physics_process(double p_delta) {
	const double dt = p_delta;

	// 重力:引擎的 get_gravity() 会带上项目设置里的默认重力
	if (!is_on_floor()) {
		Vector3 velocity = get_velocity();
		velocity.y -= get_gravity().y * gravity_scale * dt;
		set_velocity(velocity);
	}

	// 水平速度直接由输入决定(按方向 * 速度)，没有惯性，
	// 这是平台跳跃最常用的手感。
	Vector3 velocity = get_velocity();
	velocity.x = direction.x * speed;
	velocity.z = direction.z * speed;
	set_velocity(velocity);

	if (jump_pressed && is_on_floor()) {
		velocity = get_velocity();
		velocity.y = -jump_velocity;
		set_velocity(velocity);
	}

	move_and_slide();
}

// ---- 供 GDScript 调用 ----
void GKCharacter::move_by_input(const Vector3 &p_direction) {
	// 归一化:否则斜向输入会比正向快 1.41 倍
	direction = p_direction.normalized();
}

void GKCharacter::apply_knockback(const Vector3 &p_impulse) {
	Vector3 velocity = get_velocity();
	velocity += p_impulse;
	set_velocity(velocity);
}

bool GKCharacter::get_is_grounded() const {
	return is_on_floor();
}

// ---- 属性访问器 ----
void GKCharacter::set_speed(double p_value) {
	speed = p_value;
}

double GKCharacter::get_speed() const {
	return speed;
}

void GKCharacter::set_jump_velocity(double p_value) {
	jump_velocity = p_value;
}

double GKCharacter::get_jump_velocity() const {
	return jump_velocity;
}

void GKCharacter::set_gravity_scale(double p_value) {
	gravity_scale = p_value;
}

double GKCharacter::get_gravity_scale() const {
	return gravity_scale;
}

void GKCharacter::set_jump_pressed(bool p_value) {
	jump_pressed = p_value;
}
