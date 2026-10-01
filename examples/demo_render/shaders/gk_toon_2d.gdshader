shader_type canvas_item;
// ============================================================================
// GK Toon 2D -- cel shading for 2D sprites and cut-out characters
//
// Chinese documentation: ../../docs/渲染-NPR卡通着色.md
// English comments only: see gk_toon.gdshader for why.
//
// 2D needs a different approach from 3D: there is no per-pixel light direction,
// so this shader fakes the light with a small set of uniforms (direction,
// colour, ambient) and supports the two options that matter for anime art:
//   * lit_mask: a grayscale texture on the light_mask channel marking which
//     pixels can receive light (e.g. keep the face lit, shade the hair)
//   * rim light, which in 2D is what separates the character from the backdrop
//
// Set light_angle_degrees to 90 for "light from above", 0 for "from the right".
// ============================================================================

render_mode blend_mix;

group_uniforms base;
uniform vec4 sprite_tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform float brightness : hint_range(0.0, 2.0) = 1.0;

group_uniforms fake_light;
uniform float light_enabled : hint_range(0.0, 1.0) = 1.0;
// Direction the light comes FROM, in degrees. 90 = from above.
uniform float light_angle_degrees : hint_range(0.0, 360.0) = 110.0;
uniform vec4 light_color : source_color = vec4(1.0, 0.97, 0.9, 1.0);
uniform vec4 ambient_color : source_color = vec4(0.62, 0.60, 0.82, 1.0);
uniform float shadow_step : hint_range(0.0, 1.0) = 0.5;
uniform float shadow_smooth : hint_range(0.001, 0.5) = 0.04;
uniform float shadow_strength : hint_range(0.0, 2.0) = 1.0;

group_uniforms mask;
// Reuses the sprite's own alpha: pixels with low alpha never get lit, which
// keeps the outline pixels dark.
uniform float mask_alpha_threshold : hint_range(0.0, 1.0) = 0.1;
// Boost shading where the sprite is darker already (a cheap "shade the hair"
// trick that needs no extra art).
uniform float darkness_boost : hint_range(0.0, 2.0) = 0.0;

group_uniforms rim;
uniform vec4 rim_color : source_color = vec4(0.8, 0.9, 1.0, 1.0);
uniform float rim_amount : hint_range(0.0, 4.0) = 0.0;
uniform float rim_width : hint_range(0.0, 8.0) = 2.0;

group_uniforms flash;
// Hit flash / damage blink: 0 = off, 1 = fully white.
uniform float flash : hint_range(0.0, 1.0) = 0.0;
uniform vec4 flash_color : source_color = vec4(1.0, 1.0, 1.0, 1.0);

void fragment() {
	vec4 tex = texture(TEXTURE, UV);
	vec4 base = tex * sprite_tint;
	base.rgb *= brightness;

	// Alpha-based mask: transparent pixels are never lit.
	float solid = smoothstep(mask_alpha_threshold, mask_alpha_threshold + 0.05, base.a);

	vec3 lit = light_color.rgb;
	vec3 shade = ambient_color.rgb;
	vec3 final_rgb = base.rgb;

	if (light_enabled > 0.5 && solid > 0.001) {
		// Fake a normal from the sprite's luminance so flat art still gets a
		// gradient. Darker pixels read as "facing away from the light".
		float lum = dot(base.rgb, vec3(0.299, 0.587, 0.114));
		float fake_normal = mix(1.0, lum * 2.0, darkness_boost);

		float angle = light_angle_degrees * (3.14159265 / 180.0);
		float facing = cos(angle) * 0.5 + 0.5;
		float raw = clamp(facing * fake_normal, 0.0, 1.0);

		float half_w = max(shadow_smooth, 0.0005) * 0.5;
		float band = smoothstep(shadow_step - half_w, shadow_step + half_w, raw);
		vec3 ramp = mix(shade * shadow_strength, lit, band);
		final_rgb = base.rgb * mix(vec3(1.0), ramp, solid);
	}

	// --- rim light: darkened by how "inside" the sprite the pixel is --------
	if (rim_amount > 0.001) {
		// Sample the alpha around the pixel to estimate distance to the edge.
		vec2 px = TEXTURE_PIXEL_SIZE * rim_width;
		float a_sum = 0.0;
		a_sum += texture(TEXTURE, UV + vec2(px.x, 0.0)).a;
		a_sum += texture(TEXTURE, UV - vec2(px.x, 0.0)).a;
		a_sum += texture(TEXTURE, UV + vec2(0.0, px.y)).a;
		a_sum += texture(TEXTURE, UV - vec2(0.0, px.y)).a;
		float edge = 1.0 - clamp(a_sum * 0.25, 0.0, 1.0);
		edge = smoothstep(0.0, 0.6, edge);
		final_rgb += rim_color.rgb * edge * rim_amount * solid;
	}

	// --- hit flash ---------------------------------------------------------
	if (flash > 0.001) {
		final_rgb = mix(final_rgb, flash_color.rgb, flash * solid);
	}

	COLOR = vec4(final_rgb, base.a);
}
