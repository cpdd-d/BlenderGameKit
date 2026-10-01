shader_type spatial;
// ============================================================================
// GK Toon -- cel / anime shading for Godot 4
//
// Chinese documentation: ../../docs/渲染-NPR卡通着色.md
// NOTE: Godot's shader compiler rejects non-ASCII characters even in comments,
//       so every .gdshader in this project uses English comments only.
//
// Design goals (aimed at Japanese-animation style characters, not a downgraded PBR):
//   * Lighting is not a gradient but hard-edged bands, configurable 2 or 3 steps.
//   * Shadow is not "darker" but shifted toward a cool tint (anime shadows are
//     usually blue/purple, never grey).
//   * light_map: sample per-object shadowing from UV2 so an artist controls the
//     shadow shapes (bangs over the face, nose shadow) instead of the light angle.
//   * face_sdf: one clean sliding shadow line on the face instead of a blotchy
//     geometric terminator -- the single biggest "looks like anime" factor.
//   * rim light pulls the character out of the background. Almost always on.
//
// Verified against Godot 4.7.2: light() exposes LIGHT_COLOR / LIGHT / NORMAL /
// VIEW / BACKLIGHT / ATTENUATION / LIGHT_IS_DIRECTIONAL. There is NO
// LIGHT_ENERGY builtin -- light energy is already folded into LIGHT_COLOR.
// ============================================================================

group_uniforms base;
uniform vec4 albedo_color : source_color = vec4(1.0, 0.932, 0.912, 1.0);
uniform sampler2D albedo_texture : source_color, hint_default_white, filter_linear_mipmap;
// 0 = 只用 albedo_color;1 = 颜色 × 贴图。
// 必须显式给值:`hint_default_white` 让未指定贴图时 texture() 恒返回白色,
// 运行时区分不出"没贴图"和"贴图是白的"(这个坑踩过,见 fragment 里的说明)。
uniform float use_albedo_texture : hint_range(0.0, 1.0) = 0.0;
uniform float albedo_strength : hint_range(0.0, 2.0) = 1.0;

group_uniforms shadow_bands;
// Per-object shadow mask from UV2 (the second UV set exported from Blender).
uniform sampler2D light_map : hint_default_white, filter_linear_mipmap;
uniform float light_map_mix : hint_range(0.0, 1.0) = 0.0;
// Where the lit/shadow border sits. Raise toward 1.0 for a smaller lit area.
uniform float shadow_step : hint_range(-1.0, 1.0) = 0.1;
// Second (darker) band. Only used when three_tone is on.
uniform float shadow_step_2 : hint_range(-1.0, 1.0) = -0.45;
uniform float three_tone : hint_range(0.0, 1.0) = 0.0;
// Anti-aliasing width of the band edge. 0.01 -> razor sharp, 0.15 -> soft.
uniform float shadow_smooth : hint_range(0.001, 0.6) = 0.02;
uniform vec4 lit_tint : source_color = vec4(1.0, 0.976, 0.914, 1.0);
uniform vec4 shadow_tint : source_color = vec4(0.706, 0.627, 0.902, 1.0);
uniform vec4 shadow_tint_2 : source_color = vec4(0.478, 0.420, 0.722, 1.0);
uniform float shadow_strength : hint_range(0.0, 4.0) = 1.0;

group_uniforms highlight;
uniform float specular_amount : hint_range(0.0, 4.0) = 0.25;
uniform float specular_size : hint_range(0.0, 1.0) = 0.75;
uniform float specular_smooth : hint_range(0.005, 0.5) = 0.05;

group_uniforms rim_light;
uniform vec4 rim_color : source_color = vec4(0.75, 0.85, 1.0, 1.0);
uniform float rim_amount : hint_range(0.0, 4.0) = 0.5;
uniform float rim_width : hint_range(0.0, 1.0) = 0.55;
uniform float rim_front : hint_range(0.0, 1.0) = 0.15;

group_uniforms backlight;
// Anime backlight: a warm fringe when the light comes from behind.
uniform vec4 backlight_color : source_color = vec4(1.0, 0.75, 0.55, 1.0);
uniform float backlight_amount : hint_range(0.0, 4.0) = 0.0;

group_uniforms face_sdf;
// Face shadow SDF -- this is what keeps an anime face from looking muddy.
//
// Why a special case: on a real head the terminator (the light/shadow border)
// runs across the geometry and looks blotchy, because the nose and the cheeks
// have wildly different normals. Anime instead draws ONE clean shadow line that
// slides left/right as the light moves, exactly like a 2D drawing. So we throw
// away the per-pixel normal and look the shadow shape up from a texture on UV2.
//
// Texture layout: a horizontal strip split in the middle. Left half = light
// coming from screen-left, right half = the mirrored copy for screen-right.
// White = lit, black = shadow. Draw it in Blender on a second UV map (UV2).
uniform sampler2D face_sdf : hint_default_white, filter_linear;
uniform float face_sdf_mix : hint_range(0.0, 1.0) = 0.0;
uniform float face_sdf_split : hint_range(0.05, 0.95) = 0.5;
// Where on the half "fully lit" sits. 0.5 pairs with a centred shadow line.
uniform float face_sdf_neutral : hint_range(0.0, 1.0) = 0.5;
uniform float face_sdf_angle_scale : hint_range(0.25, 4.0) = 1.0;
uniform float face_sdf_smooth : hint_range(0.001, 0.5) = 0.02;
// Vertical nudge for the shadow line.
uniform float face_sdf_v_offset : hint_range(-0.5, 0.5) = 0.0;

group_uniforms misc;
uniform float emission_amount : hint_range(0.0, 8.0) = 0.0;
uniform float alpha_cut : hint_range(0.0, 1.0) = 0.5;
uniform float eye_highlight_boost : hint_range(0.0, 8.0) = 0.0;

// World position, used by the outline pass (next_pass) to keep width constant.
varying vec3 gk_world_pos;
// UV2 is only available in vertex()/fragment(), NOT in light(). The face SDF
// needs it inside light(), so it has to be carried across as a varying.
varying vec2 gk_uv2;


// A near-hard step that still antialiases. This is the heart of cel shading:
// a smooth function squeezed into a very narrow band.
float gk_band(float x, float edge, float width) {
	float half_w = max(width, 0.0005) * 0.5;
	return smoothstep(edge - half_w, edge + half_w, x);
}

// Map raw lighting to the tint ramp (dark -> mid -> lit).
vec3 gk_ramp(float raw) {
	float band_lit = gk_band(raw, shadow_step, shadow_smooth);
	vec3 mid = mix(shadow_tint_2.rgb, shadow_tint.rgb, band_lit);
	vec3 ramp = mix(mid, lit_tint.rgb, band_lit);
	if (three_tone > 0.5) {
		float band_mid = gk_band(raw, shadow_step_2, shadow_smooth);
		vec3 three = mix(shadow_tint_2.rgb, shadow_tint.rgb, band_mid);
		ramp = mix(three, lit_tint.rgb, band_lit);
	}
	return ramp;
}

// Face shadow lookup.
// light_dir: normalized direction from the fragment toward the light (view space)
// view_dir:  normalized direction from the fragment toward the camera (view space)
//
// Returns a value in the same -1..1 space gk_ramp expects.
float gk_face_shadow(vec3 light_dir, vec3 view_dir) {
	// Signed horizontal position of the light relative to the camera.
	// cross(view, light).y is positive when the light is to screen-right,
	// negative to screen-left, ~0 when it is straight ahead or directly behind.
	float side = cross(view_dir, light_dir).y;

	// Light behind the head: the whole face should read as shadow, which is
	// what the reference art does (no stray lit patches on the cheeks).
	float behind = clamp(-dot(view_dir, light_dir) * 1.5, 0.0, 1.0);

	float amount = clamp(abs(side) * face_sdf_angle_scale, 0.0, 1.0);

	// Pick the half of the strip and the direction the shadow line travels.
	float split = clamp(face_sdf_split, 0.05, 0.95);
	float u_lo = 0.0;
	float u_hi = split;
	float line = face_sdf_neutral;
	if (side >= 0.0) {
		// Light to screen-right: read the mirrored half, line slides left.
		u_lo = split;
		u_hi = 1.0;
		line = mix(face_sdf_neutral, 0.0, amount);
	} else {
		// Light to screen-left: read the left half, line slides right.
		line = mix(face_sdf_neutral, 1.0, amount);
	}

	vec2 sdf_uv = vec2(mix(u_lo, u_hi, line), gk_uv2.y + face_sdf_v_offset);
	float mask = texture(face_sdf, sdf_uv).r;
	mask *= 1.0 - behind;

	// mask is white=lit / black=shadow. Convert to -1..1, then re-sharpen:
	// the texture has soft edges, but we want the cel border back.
	float raw = mask * 2.0 - 1.0;
	return gk_band(raw, -0.5 + shadow_smooth, face_sdf_smooth) * 2.0 - 1.0;
}

void vertex() {
	gk_world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	gk_uv2 = UV2;
}

void fragment() {
	// ------------------------------------------------------------------
	// albedo:颜色 / 贴图 / 两者相乘
	//
	// 为什么要一个 use_albedo_texture 开关,而不是靠"贴图是不是 null"判断:
	// `hint_default_white` 让未指定贴图时 texture() **永远返回白色**,
	// 这个行为没有开关、也不受 .tres 里 albedo_texture = null 影响。
	// 所以运行时区分不出"没给贴图"和"给了一张纯白贴图"。
	//
	// 实测后果:材质设了 albedo_color = 红色,渲染出来却是白的 ——
	// 因为白贴图乘红色虽然得到红色,但整条卡通链路后来又用
	// lit_tint / shadow_tint 覆盖,视觉上完全看不出材质本身的颜色。
	// 排查这个花了很久,所以在这里写成显式开关。
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

	ALBEDO = base.rgb;
	ALPHA = alpha;
	METALLIC = 0.0;
	// Fully rough + zero specular disables the engine's own soft highlight so
	// only our hard-edged cel highlight is visible. (There is no
	// SPECULAR_LIGHT builtin available inside light() in Godot 4.7 -- verified
	// empirically -- so the cel highlight has to be computed here and pushed
	// through EMISSION.)
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;

	// --- cel highlight: a hard-edged spot instead of a soft falloff ---------
	// Uses a fixed world-space half-vector instead of the real light direction,
	// which is what character artists usually want: the highlight stays "drawn
	// on" the surface instead of swimming as the light moves.
	if (specular_amount > 0.001) {
		vec3 view_dir = normalize(VIEW);
		vec3 half_vec = normalize(view_dir + normalize(vec3(0.35, 0.9, 0.45)));
		float spot = gk_band(dot(normalize(NORMAL), half_vec), 1.0 - specular_size, specular_smooth);
		EMISSION += vec3(spot * specular_amount);
	}

	// --- rim light ---------------------------------------------------------
	if (rim_amount > 0.001) {
		vec3 view_dir = normalize(VIEW);
		float nv = dot(normalize(NORMAL), view_dir);
		float rim = 1.0 - smoothstep(rim_width, 1.0, nv);
		// rim_front pushes a little of the rim onto the front so the silhouette
		// does not read as a separate outline.
		rim = clamp(rim + rim_front * (1.0 - nv), 0.0, 1.0);
		EMISSION += rim_color.rgb * rim * rim_amount;
	}

	if (emission_amount > 0.001) {
		EMISSION += base.rgb * emission_amount;
	}
	if (eye_highlight_boost > 0.001) {
		// Cheap trick: bright pixels (eye highlights, specular dots painted into
		// the texture) glow slightly, which makes the eyes read at small sizes.
		float bright = smoothstep(0.75, 1.0, dot(base.rgb, vec3(0.333)));
		EMISSION += base.rgb * bright * eye_highlight_boost;
	}
}

void light() {
	// ========================================================================
	// Replace the engine's continuous lambert term with a banded one.
	// Only diffuse light is overridden; specular is accumulated in fragment().
	// ========================================================================
	vec3 light_dir = normalize(LIGHT);
	vec3 normal = normalize(NORMAL);
	float raw = dot(normal, light_dir);

	// --- face shadow SDF ---------------------------------------------------
	// Replaces the geometric term entirely on the face: one clean shadow line
	// instead of a blotchy terminator. Applied before light_map so a project
	// can still layer a hand-painted mask on top.
	if (face_sdf_mix > 0.001) {
		float face = gk_face_shadow(light_dir, normalize(VIEW));
		raw = mix(raw, face, face_sdf_mix);
	}

	// Per-object shadow mask. light_map is white by default, so mix() with a
	// zero factor leaves the geometric term untouched.
	if (light_map_mix > 0.001) {
		float mask = texture(light_map, gk_uv2).r;
		raw = mix(raw, mask * 2.0 - 1.0, light_map_mix);
	}

	vec3 ramp = gk_ramp(raw);
	// LIGHT_COLOR already carries the light's energy and attenuation for this
	// light, so we do not multiply by anything else.
	//
	// **必须乘 ALBEDO**。原版只写 `DIFFUSE_LIGHT += ramp * LIGHT_COLOR`,
	// 于是最终颜色完全由色阶和光色决定,材质本身的颜色被丢掉了 ——
	// 不论 albedo_color 设成什么,渲染出来都是白/粉。
	// 实测:球材质设成红色,渲染却是白色,排查了很久。
	// ALBEDO 在 light() 里可直接读(Godot 会为此生成对应的 varying)。
	DIFFUSE_LIGHT += ramp * ALBEDO * LIGHT_COLOR * shadow_strength;

	// --- backlight fringe --------------------------------------------------
	if (backlight_amount > 0.001) {
		float back = dot(normal, -light_dir);
		float fringe = gk_band(back, 0.35, shadow_smooth * 4.0);
		DIFFUSE_LIGHT += backlight_color.rgb * fringe * backlight_amount * LIGHT_COLOR;
	}
}
