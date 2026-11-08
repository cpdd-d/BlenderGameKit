shader_type spatial;
// Scene-lit toon: each real light supplies its direction, energy, cone,
// distance falloff and shadow visibility. Fixed studio lighting uses gk_toon.
render_mode depth_draw_opaque, cull_disabled;

uniform vec4 albedo_color : source_color = vec4(1.0);
uniform sampler2D albedo_texture : source_color, hint_default_white, filter_linear_mipmap;
uniform float use_albedo_texture = 0.0;
uniform float albedo_strength = 1.0;
uniform float alpha_cut = 0.5;
uniform float ambient : hint_range(0.0, 1.0) = 1.0;
uniform float shadow_step = 0.05;
uniform float shadow_step_2 = -0.45;
uniform float shadow_smooth = 0.03;
uniform float three_tone = 0.0;
uniform vec4 lit_tint : source_color = vec4(1.0, 0.98, 0.94, 1.0);
uniform vec4 shadow_tint : source_color = vec4(0.72, 0.66, 0.90, 1.0);
uniform vec4 shadow_tint_2 : source_color = vec4(0.50, 0.45, 0.74, 1.0);
uniform float specular_amount = 0.0;
uniform float specular_size = 0.75;
uniform float specular_smooth = 0.05;
uniform vec4 specular_color : source_color = vec4(1.0);
uniform sampler2D specular_shape_texture : hint_default_white, filter_linear, repeat_disable;
uniform float use_specular_shape_texture = 0.0;
uniform sampler2D specular_mask_texture : hint_default_white, filter_linear, repeat_disable;
uniform float use_specular_mask_texture = 0.0;
uniform vec4 rim_color : source_color = vec4(0.75, 0.85, 1.0, 1.0);
uniform float rim_amount = 0.0;
uniform float rim_width = 0.55;
uniform float rim_front = 0.15;
uniform float emission_amount = 0.0;
// 自发光的**颜色**(第一百四十八轮)。
// 以前只有 emission_amount 一个标量,而且永远乘在 ALBEDO 上 —— 灯罩
// 这种"深色金属 + 暖光"的材质根本发不出光(深色 × 强度 还是深色)。
// 现在颜色和强度分开:EMISSION = emission_color × amount,和 albedo 无关。
uniform vec4 emission_color : source_color = vec4(1.0);
uniform float eye_highlight_boost = 0.0;

// Anime face shading: one clean shadow line looked up from a texture instead
// of the blotchy geometric terminator. Same strip layout as gk_toon.gdshader.
uniform sampler2D face_sdf : hint_default_white, filter_linear, repeat_disable;
uniform float face_sdf_mix : hint_range(0.0, 1.0) = 0.0;
uniform float face_sdf_split : hint_range(0.05, 0.95) = 0.5;
uniform float face_sdf_neutral : hint_range(0.0, 1.0) = 0.5;
uniform float face_sdf_angle_scale : hint_range(0.25, 4.0) = 1.6;
uniform float face_sdf_smooth : hint_range(0.001, 0.5) = 0.03;
uniform float face_sdf_v_offset : hint_range(-0.5, 0.5) = 0.0;
uniform float face_sdf_use_uv2 : hint_range(0.0, 1.0) = 0.0;

varying vec2 gk_uv;
varying vec2 gk_uv2;

float gk_band(float x, float edge, float width) {
	float half_w = max(width, 0.0005) * 0.5;
	return smoothstep(edge - half_w, edge + half_w, x);
}

float gk_face_shadow(vec3 light_view, vec3 view_dir, vec2 base_uv) {
	float side = cross(view_dir, light_view).y;
	float behind = clamp(dot(view_dir, light_view) * 1.5, 0.0, 1.0);
	float amount = clamp(abs(side) * face_sdf_angle_scale, 0.0, 1.0);

	float line = face_sdf_neutral;
	if (side >= 0.0) {
		line = mix(face_sdf_neutral, 0.0, amount);
	} else {
		line = mix(face_sdf_neutral, 1.0, amount);
	}

	vec2 sdf_uv = vec2(base_uv.x + (line - face_sdf_neutral),
		base_uv.y + face_sdf_v_offset);
	float mask = texture(face_sdf, sdf_uv).r;
	mask *= 1.0 - behind;
	float raw = mask * 2.0 - 1.0;
	return gk_band(raw, -0.5 + shadow_smooth, face_sdf_smooth) * 2.0 - 1.0;
}

void vertex() {
	gk_uv2 = UV2;
}

void fragment() {
	vec4 base = albedo_color;
	if (use_albedo_texture > 0.5) {
		base *= texture(albedo_texture, UV);
	}
	if (base.a < alpha_cut) {
		discard;
	}
	ALBEDO = base.rgb * albedo_strength;
	METALLIC = 0.0;
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;
	AO = clamp(ambient, 0.0, 1.0);
	gk_uv = UV;
	// Only explicit emission may glow when all scene lights are off.
	float eye = smoothstep(0.75, 1.0, dot(base.rgb, vec3(0.333)));
	EMISSION = emission_color.rgb * emission_amount
		+ ALBEDO * eye * eye_highlight_boost;
}

void light() {
	float raw = dot(NORMAL, LIGHT);
	if (face_sdf_mix > 0.001) {
		vec2 base_uv = face_sdf_use_uv2 > 0.5 ? gk_uv2 : gk_uv;
		raw = mix(raw, gk_face_shadow(normalize(LIGHT), normalize(VIEW), base_uv),
			face_sdf_mix);
	}
	vec3 dark = shadow_tint.rgb;
	if (three_tone > 0.5) {
		dark = mix(shadow_tint_2.rgb, dark,
			gk_band(raw, shadow_step_2, shadow_smooth));
	}
	vec3 tint = mix(dark, lit_tint.rgb,
		gk_band(raw, shadow_step, shadow_smooth));
	// Godot applies ALBEDO after light(); multiplying it here would square it.
	float diffuse = max(raw, 0.0) * ATTENUATION;
	if (three_tone > 0.5) {
		// ── 三层色阶(第一百五十一轮)────────────────────────────────
		// 用户原话:"阴影的渲染上太单调了,不是浅黑、深黑、暗黑这样的过渡"。
		// 旧写法只让**朝向光的面**吃到光(`max(raw, 0)`),背光面直接归零,
		// 于是整片墙永远只有环境光一个色 —— shadow_tint_2 根本没机会出现。
		// 这里改成两层三档:
		//   * 朝向:亮 / 浅黑 / 深黑(用 shadow_tint -> shadow_tint_2);
		//   * 光池:满 / 半 / 无(ATTENUATION 的三档,弱光就是"暗黑")。
		// ⚠ 过渡带必须**宽**(0.30),否则灯池会变成"同心圆靶心",
		//   用户第二轮反馈:"灯光的范围显示过于完美了,局部明暗不缓和"。
		//   宽过渡带 = 三档还在,但边界是渐变的,肉眼读成柔和的明暗过渡。
		float facing_hi = gk_band(raw, shadow_step, shadow_smooth);
		float facing_mid = gk_band(raw, shadow_step_2, shadow_smooth);
		float facing = mix(0.22, mix(0.55, 1.0, facing_hi), facing_mid);
		float pool_hi = gk_band(ATTENUATION, 0.58, 0.30);
		float pool_mid = gk_band(ATTENUATION, 0.22, 0.30);
		float pool = mix(0.0, mix(0.64, 1.0, pool_hi), pool_mid);
		diffuse = facing * pool;
	}
	DIFFUSE_LIGHT += tint * diffuse * LIGHT_COLOR / PI;
	if (specular_amount > 0.001) {
		float half_spot = dot(NORMAL, normalize(VIEW + LIGHT));
		float spot = gk_band(half_spot, 1.0 - specular_size, specular_smooth);
		if (use_specular_shape_texture > 0.5) {
			float u = clamp((half_spot - (1.0 - specular_size))
				/ max(specular_size, 0.0001), 0.0, 1.0);
			spot = texture(specular_shape_texture, vec2(u, 0.5)).r;
		}
		if (use_specular_mask_texture > 0.5) {
			spot *= texture(specular_mask_texture, gk_uv).r;
		}
		SPECULAR_LIGHT += specular_color.rgb * spot * specular_amount
			* max(raw, 0.0) * ATTENUATION * LIGHT_COLOR / PI;
	}
	if (rim_amount > 0.001) {
		float nv = dot(NORMAL, VIEW);
		float rim = clamp(1.0 - smoothstep(rim_width, 1.0, nv)
			+ rim_front * (1.0 - nv), 0.0, 1.0);
		SPECULAR_LIGHT += rim_color.rgb * rim * rim_amount
			* max(raw, 0.0) * ATTENUATION * LIGHT_COLOR / PI;
	}
}
