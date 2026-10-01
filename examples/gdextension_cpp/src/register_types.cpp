#include "register_types.h"
#include "gk_character.h"

#include <godot_cpp/core/defs.hpp>
#include <godot_cpp/godot.hpp>

using namespace godot;

// ============================================================================
// GDExtension 注册入口
//
// entry_symbol 必须和 gk_demo.gdextension 里的 entry_symbol 一致。
// Godot 加载 .dll 后调用它，在这里把自定义类注册进 ClassDB。
// 注册之后，GDScript 就能直接 new 出来或者用 class_name 引用。
// ============================================================================

static void initialize_gk_module(ModuleInitializationLevel p_level) {
	if (p_level != MODULE_INITIALIZATION_LEVEL_SCENE) {
		return;
	}
	GDREGISTER_CLASS(GKCharacter);
}

static void uninitialize_gk_module(ModuleInitializationLevel p_level) {
	if (p_level != MODULE_INITIALIZATION_LEVEL_SCENE) {
		return;
	}
}

extern "C" {

// 这个函数名必须和 .gdextension 里的 entry_symbol 一致
GDExtensionBool GDE_EXPORT gk_library_init(
		GDExtensionInterfaceGetProcAddress p_get_proc_address,
		const GDExtensionClassLibraryPtr p_library,
		GDExtensionInitialization *r_initialization) {
	godot::GDExtensionBinding::InitObject init_obj(p_get_proc_address, p_library, r_initialization);

	init_obj.register_initializer(initialize_gk_module);
	init_obj.register_terminator(uninitialize_gk_module);
	init_obj.set_minimum_library_initialization_level(MODULE_INITIALIZATION_LEVEL_SCENE);

	return init_obj.init();
}
}
