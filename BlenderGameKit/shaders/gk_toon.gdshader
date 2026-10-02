shader_type spatial;
// ============================================================================
// GK Toon -- cel / anime shading for Godot 4   (self-lit version)
//
// Chinese documentation: ../../docs/渲染-NPR卡通着色.md
// NOTE: Godot's shader compiler rejects non-ASCII characters even in comments,
//       so every .gdshader in this project uses English comments only.
//
// ============================================================================
// WHY THIS SHADER IS `unshaded` -- the investigation that led here
// ============================================================================
// The previous version used `light()` to replace Godot's lambert term with a
// banded ramp:
//
//     void light() {
//         vec3 ramp = gk_ramp(dot(NORMAL, LIGHT));
//         DIFFUSE_LIGHT += ramp * ALBEDO * LIGHT_COLOR * shadow_strength;
//     }
//
// Extensive empirical testing on Godot 4.7.2 showed that **this contribution
// is negligible in the final image**. Evidence (a red sphere + one
// DirectionalLight3D with light_energy 4.0, compared against the same sphere
// with a StandardMaterial3D):
//
//   * Sweeping `toon_exposure` 0.0 -> 0.25 -> 0.5 -> 1.0 -> 2.0 -> 4.0
//     produced essentially identical output. If `light()` were driving the
//     colour, a 16x change in its multiplier would be impossible to miss.
//   * Setting lit_tint / shadow_tint / shadow_tint_2 all to pure white
//     changed nothing.
//   * A sphere rendered with this shader came out a flat washed-out pink with
//     a bright rim, while the StandardMaterial3D sphere next to it under the
//     *same light* showed proper red shading.
//   * A control test with a minimal hand-written shader proved `light()` in
//     Godot 4.7 *does* affect the result (writing pure green there turned the
//     sphere green), so the mechanism works -- our expression was simply
//     producing a near-zero value.
//
// Diagnosis: the visible result was coming from the engine's ambient/IBL term
// (which multiplies ALBEDO but never passes through `light()`), plus the
// additive EMISSION terms in fragment(). The banded ramp we so carefully
// designed was effectively dead code, and the "toon look" was really just the
// texture plus ambient wash.
//
// Rather than keep fighting an opaque pipeline, this version takes the direct
// route: `unshaded` means Godot applies **no** lighting of its own, and we
// compute the entire cel look ourselves in fragment() from explicit uniforms.
// That gives three concrete wins:
//
//   1. The bands are exactly where we put them -- no ambient wash on top.
//   2. The result does not depend on how many lights the scene happens to
//      have, their energy, or Godot's light-culling. One character looks the
//      same in every scene, which is what an artist wants.
//   3. Everything is inspectable and tweakable from the material inspector.
//
// The trade-off is deliberate and worth stating: this shader ignores scene
// lights entirely. `light_dir` is a material parameter, not the sun's actual
// direction. Projects that need real-time day/night lighting should drive
// `light_dir` from GDScript (see gk_environment.gd) or switch back to a
// `light()`-based shader.
// ============================================================================

// ── cull_disabled 是**必须的**,不是可选优化 ──────────────────────────
// MMD 模型(.pmx 经 mmd_tools 导入)大量使用**单面片**做头发/帽子/裙摆,
// 且不少面片是反的。开启默认的背面剔除后:
//   * 反向面片被整个剔掉 -> 帽子/头发**大面积变黑或消失**
//     (用户实测:"模型显示得有点诡异")
// 关掉剔除后双面都画,代价是三角形数量翻倍的填充率,对角色模型可以接受。
render_mode unshaded, cull_disabled;

group_uniforms base;
uniform vec4 albedo_color : source_color = vec4(1.0, 0.932, 0.912, 1.0);
uniform sampler2D albedo_texture : source_color, hint_default_white, filter_linear_mipmap;
// 0 = albedo_color only; 1 = albedo_color * texture.
// Must be explicit: `hint_default_white` makes texture() always return white
// when unset, so the shader cannot tell "no texture" from "a white texture".
uniform float use_albedo_texture : hint_range(0.0, 1.0) = 0.0;
uniform float albedo_strength : hint_range(0.0, 2.0) = 1.0;

group_uniforms light_source;
// Direction the light comes FROM, in view space (camera space).
// Default is upper-left-front, the classic anime key light.
// View space means the shading follows the camera, which is normal for a
// character-focused cel look and keeps the face readable from any angle.
uniform vec3 light_dir = vec3(-0.35, 0.75, 0.55);
uniform vec4 light_color : source_color = vec4(1.0, 0.98, 0.95, 1.0);
// Overall brightness. 1.0 is "as authored"; lower it to sit a character into
// a darker scene without touching the tints.
uniform float light_energy : hint_range(0.0, 4.0) = 1.0;
// How much of the texture shows in fully shadowed areas. Anime shadows are
// tinted, not black, so this stays fairly high.
uniform float ambient : hint_range(0.0, 1.0) = 0.55;

group_uniforms shadow_bands;
// Where the lit/shadow border sits. -1 = fully lit, 1 = fully shadowed.
uniform float shadow_step : hint_range(-1.0, 1.0) = 0.05;
// Second (darker) band. Only used when three_tone is on.
uniform float shadow_step_2 : hint_range(-1.0, 1.0) = -0.45;
uniform float three_tone : hint_range(0.0, 1.0) = 0.0;
// Anti-aliasing width of the band edge. 0.01 -> razor sharp, 0.15 -> soft.
uniform float shadow_smooth : hint_range(0.001, 0.6) = 0.03;
// Colour of lit areas. Slightly warm by default.
uniform vec4 lit_tint : source_color = vec4(1.0, 0.98, 0.94, 1.0);
// Anime shadows shift toward cool blue/purple instead of going grey.
uniform vec4 shadow_tint : source_color = vec4(0.72, 0.66, 0.90, 1.0);
uniform vec4 shadow_tint_2 : source_color = vec4(0.50, 0.45, 0.74, 1.0);

group_uniforms highlight;
uniform float specular_amount : hint_range(0.0, 4.0) = 0.0;
uniform float specular_size : hint_range(0.0, 1.0) = 0.75;
uniform float specular_smooth : hint_range(0.005, 0.5) = 0.05;
uniform vec4 specular_color : source_color = vec4(1.0, 1.0, 1.0, 1.0);

group_uniforms rim_light;
uniform vec4 rim_color : source_color = vec4(0.75, 0.85, 1.0, 1.0);
uniform float rim_amount : hint_range(0.0, 4.0) = 0.0;
uniform float rim_width : hint_range(0.0, 1.0) = 0.55;
uniform float rim_front : hint_range(0.0, 1.0) = 0.15;

group_uniforms misc;
uniform float alpha_cut : hint_range(0.0, 1.0) = 0.5;
uniform float emission_amount : hint_range(0.0, 8.0) = 0.0;
uniform float eye_highlight_boost : hint_range(0.0, 8.0) = 0.0;

// World position, used by the outline pass (next_pass) to keep width constant.
varying vec3 gk_world_pos;

// A near-hard step that still antialiases. This is the heart of cel shading:
// a smooth function squeezed into a very narrow band.
float gk_band(float x, float edge, float width) {
	float half_w = max(width, 0.0005) * 0.5;
	return smoothstep(edge - half_w, edge + half_w, x);
}

// Map raw lighting (-1..1) to the tint ramp (shadow2 -> shadow -> lit).
vec3 gk_ramp(float raw) {
	float band_lit = gk_band(raw, shadow_step, shadow_smooth);
	if (three_tone > 0.5) {
		float band_mid = gk_band(raw, shadow_step_2, shadow_smooth);
		vec3 three = mix(shadow_tint_2.rgb, shadow_tint.rgb, band_mid);
		return mix(three, lit_tint.rgb, band_lit);
	}
	vec3 mid = mix(shadow_tint_2.rgb, shadow_tint.rgb, band_lit);
	return mix(mid, lit_tint.rgb, band_lit);
}

void vertex() {
	gk_world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	// ------------------------------------------------------------------
	// albedo: colour / texture / both multiplied
	// ------------------------------------------------------------------
	vec4 base = albedo_color;
	if (use_albedo_texture > 0.5) {
		base *= texture(albedo_texture, UV);
	}
	base.rgb *= albedo_strength;

	float alpha = base.a;
	if (alpha < alpha_cut) {
		discard;
	}

	// ------------------------------------------------------------------
	// Cel shading, computed entirely by us (the shader is `unshaded`).
	//
	// NORMAL and VIEW arrive in view space, so light_dir is view space too.
	// ------------------------------------------------------------------
	vec3 n = normalize(NORMAL);
	vec3 v = normalize(VIEW);
	vec3 l = normalize(light_dir);

	// A plain dot keeps the terminator crisp, which is the whole point of a
	// cel look. (A half-lambert wrap would smear it out.)
	float raw = dot(n, l);

	float lit_factor = gk_band(raw, shadow_step, shadow_smooth);
	vec3 ramp = gk_ramp(raw);

	// Anime shadows are a TINT, not a darkening: shadowed areas keep
	// `ambient` of the texture colour, pulled toward shadow_tint. Lit areas
	// get the full banded ramp. Mixing the two by lit_factor keeps the
	// transition exactly as hard as shadow_smooth says -- no ambient wash
	// leaking over the band edge.
	vec3 shadow_col = base.rgb * shadow_tint.rgb;
	vec3 lit_col = base.rgb * ramp * light_color.rgb * light_energy;
	float shadow_keep = clamp(ambient, 0.0, 1.0);
	vec3 cel = mix(shadow_col * shadow_keep, lit_col, lit_factor);

	// Floor: never let a texel go completely black. Fully shadowed anime
	// characters are still readable, not silhouettes.
	cel = max(cel, base.rgb * shadow_tint.rgb * shadow_keep * 0.85);

	ALBEDO = cel;
	ALPHA = 1.0;
	METALLIC = 0.0;
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;

	// --- cel highlight: a hard-edged spot instead of a soft falloff ---------
	// Fixed half-vector so the highlight stays "drawn on" the surface instead
	// of swimming as the light moves.
	if (specular_amount > 0.001) {
		vec3 half_vec = normalize(v + l);
		float spot = gk_band(dot(n, half_vec), 1.0 - specular_size, specular_smooth);
		EMISSION += specular_color.rgb * spot * specular_amount;
	}

	// --- rim light ---------------------------------------------------------
	if (rim_amount > 0.001) {
		float nv = dot(n, v);
		float rim = 1.0 - smoothstep(rim_width, 1.0, nv);
		rim = clamp(rim + rim_front * (1.0 - nv), 0.0, 1.0);
		EMISSION += rim_color.rgb * rim * rim_amount;
	}

	if (emission_amount > 0.001) {
		EMISSION += base.rgb * emission_amount;
	}
	if (eye_highlight_boost > 0.001) {
		// Bright pixels (eye highlights painted into the texture) glow
		// slightly, which makes the eyes read at small sizes.
		float bright = smoothstep(0.75, 1.0, dot(base.rgb, vec3(0.333)));
		EMISSION += base.rgb * bright * eye_highlight_boost;
	}
}
