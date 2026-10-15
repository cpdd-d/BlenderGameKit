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
uniform float eye_highlight_boost = 0.0;
varying vec2 gk_uv;

float gk_band(float x, float edge, float width) {
	float half_w = max(width, 0.0005) * 0.5;
	return smoothstep(edge - half_w, edge + half_w, x);
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
	EMISSION = ALBEDO * (emission_amount + eye * eye_highlight_boost);
}

void light() {
	float raw = dot(NORMAL, LIGHT);
	vec3 dark = shadow_tint.rgb;
	if (three_tone > 0.5) {
		dark = mix(shadow_tint_2.rgb, dark,
			gk_band(raw, shadow_step_2, shadow_smooth));
	}
	vec3 tint = mix(dark, lit_tint.rgb,
		gk_band(raw, shadow_step, shadow_smooth));
	// Godot applies ALBEDO after light(); multiplying it here would square it.
	DIFFUSE_LIGHT += tint * max(raw, 0.0) * ATTENUATION * LIGHT_COLOR / PI;
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
