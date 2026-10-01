shader_type spatial;
// ============================================================================
// GK Outline -- inverted-hull outline for anime characters, with a
// hand-drawn ("boiling line") animation option.
//
// Chinese documentation: ../../docs/渲染-NPR卡通着色.md
// English comments only: Godot's shader compiler rejects non-ASCII in comments.
//
// How to use: this is meant as a *next_pass* on a ShaderMaterial that uses
// gk_toon.gdshader. In the inspector:
//     Material -> Next Pass -> New ShaderMaterial -> shader = gk_outline.gdshader
//
// Why inverted hull instead of a screen-space edge pass:
//   * works with any geometry, no normal/depth buffers required
//   * the line can be animated per vertex (see the jitter section below)
//   * drawback: hard-edged geometry (cubes) shows gaps at the corners.
//     Raise outline_width for those cases.
//
// ---------------------------------------------------------------------------
// HAND-DRAWN LINE ANIMATION ("boiling")
//
// Real hand-drawn animation is not smooth: lines are redrawn every 2 or 3
// frames, so they shimmer and breathe. Reproducing that needs three things,
// and getting any one of them wrong makes the result look wrong:
//
//   1. STEPPED TIME. The jitter must hold still between animation frames and
//      then snap. `jitter_fps` is that animation framerate -- 8 or 12 is the
//      classic "on twos / on threes" feel. A smooth TIME would look like
//      vibration, not drawing.
//   2. VALUE NOISE, not random. Pure random per vertex tears the line into
//      disconnected segments. Smooth noise keeps neighbouring vertices moving
//      together, which is what reads as a wobbly drawn line.
//   3. LOW AMPLITUDE. The line should breathe, not vibrate. If you can see it
//      as an animation rather than as texture, the amplitude is too high.
//
// NOTE: Godot 4.7 exposes TIME but NOT FRAME / FRAME_NUMBER / RENDER_PASS /
// VERTEX_ID / INSTANCE_ID (verified empirically with scripts/verify_shaders.py).
// That is why the step is computed here from TIME instead of reading a frame
// counter -- and it is also what lets jitter_fps be arbitrary rather than
// locked to the render framerate.
// ============================================================================

render_mode unshaded, cull_front, depth_draw_opaque;

group_uniforms outline;
uniform vec4 outline_color : source_color = vec4(0.06, 0.05, 0.09, 1.0);
// Width in world units. 0.01 ~ 0.04 is the usual range for a character.
uniform float outline_width : hint_range(0.0, 0.3) = 0.018;
// Constant screen size: keeps the outline from thinning out with distance.
uniform float outline_screen_constant : hint_range(0.0, 1.0) = 0.0;
// Extra width added by how "side-on" the surface is, which fattens the
// silhouette and thins the parts facing the camera.
uniform float outline_silhouette_boost : hint_range(0.0, 2.0) = 0.0;
uniform float outline_alpha : hint_range(0.0, 1.0) = 1.0;
// 0 = draw only the hull pass, 1 = normal. Internally kept for the demo.
uniform float outline_only : hint_range(0.0, 1.0) = 1.0;

group_uniforms hand_drawn;
// Master switch for the whole hand-drawn effect. 0 = clean, static line.
uniform float jitter_enable : hint_range(0.0, 1.0) = 0.0;
// Animation framerate of the line. 8 ~ 12 is the classic hand-drawn feel;
// 24+ starts looking like vibration instead of drawing.
uniform float jitter_fps : hint_range(1.0, 30.0) = 10.0;
// How much the WIDTH breathes, relative to outline_width. 0.15 ~ 0.4 reads well.
uniform float jitter_width : hint_range(0.0, 1.0) = 0.25;
// How much the whole line SLIDES sideways, in world units.
// This is what breaks the "perfectly traced silhouette" look.
uniform float jitter_offset : hint_range(0.0, 0.05) = 0.004;
// Spatial size of the noise. Smaller = more wobbles along the line.
uniform float jitter_scale : hint_range(0.5, 40.0) = 6.0;
// Per-vertex variation. 0 makes the whole line move as one (very "paper
// cut-out"); 1 lets different parts of the line wobble independently.
uniform float jitter_vertex_seed : hint_range(0.0, 1.0) = 0.6;
// Extra width applied as a whole, for impact frames. Drive this from GDScript
// with a tween for a "line pops on hit" effect.
uniform float impact_boost : hint_range(0.0, 2.0) = 0.0;

varying vec3 gk_world_pos;

// ---------------------------------------------------------------------------
// Value noise. Written by hand on purpose -- see the note above about why we
// cannot rely on a noise TEXTURE (hint_default_white would force it white when
// no texture is assigned, and requiring an asset makes the shader fragile).
// ---------------------------------------------------------------------------
float gk_hash(vec3 p) {
	// A cheap but well-distributed hash, the standard "sin dot" trick with
	// large multipliers to avoid visible banding.
	return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
}

float gk_value_noise(vec3 p) {
	vec3 i = floor(p);
	vec3 f = fract(p);
	// Smoothstep the interpolant so the noise is C1 continuous. Without this,
	// the line gets visible faceted kinks at cell borders.
	vec3 u = f * f * (3.0 - 2.0 * f);

	float n000 = gk_hash(i + vec3(0.0, 0.0, 0.0));
	float n100 = gk_hash(i + vec3(1.0, 0.0, 0.0));
	float n010 = gk_hash(i + vec3(0.0, 1.0, 0.0));
	float n110 = gk_hash(i + vec3(1.0, 1.0, 0.0));
	float n001 = gk_hash(i + vec3(0.0, 0.0, 1.0));
	float n101 = gk_hash(i + vec3(1.0, 0.0, 1.0));
	float n011 = gk_hash(i + vec3(0.0, 1.0, 1.0));
	float n111 = gk_hash(i + vec3(1.0, 1.0, 1.0));

	float nx00 = mix(n000, n100, u.x);
	float nx10 = mix(n010, n110, u.x);
	float nx01 = mix(n001, n101, u.x);
	float nx11 = mix(n011, n111, u.x);

	float nxy0 = mix(nx00, nx10, u.y);
	float nxy1 = mix(nx01, nx11, u.y);

	return mix(nxy0, nxy1, u.z);
}

void vertex() {
	gk_world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;

	// Work in view space so the offset is not distorted by non-uniform scale.
	vec4 view_pos = MODELVIEW_MATRIX * vec4(VERTEX, 1.0);
	vec3 view_normal = normalize(mat3(MODELVIEW_MATRIX) * NORMAL);

	// Screen-constant width: scale the offset by the distance to the camera.
	float width = outline_width;
	if (outline_screen_constant > 0.001) {
		float dist = max(abs(view_pos.z), 0.001);
		width *= mix(1.0, dist, outline_screen_constant);
	}

	// Silhouette boost: surfaces facing away from the camera get a thicker edge.
	vec3 to_cam = normalize(-view_pos.xyz);
	float facing = abs(dot(view_normal, to_cam));
	width *= 1.0 + outline_silhouette_boost * (1.0 - facing);

	// Impact frames: a global width kick, meant to be tweened from GDScript.
	width *= 1.0 + impact_boost;

	vec3 line_offset = vec3(0.0);

	if (jitter_enable > 0.5) {
		// --- 1. stepped time -------------------------------------------------
		// Snap to the animation framerate. Each drawn "frame" holds a constant
		// noise value, so the line stays put and then jumps -- that is what
		// reads as hand-drawn. Multiplying by jitter_fps and flooring gives us
		// the animation frame index without needing a FRAME builtin.
		float step_index = floor(TIME * jitter_fps);

		// --- 2. spatial coordinate ------------------------------------------
		// Use the vertex's WORLD position so the noise is stable in space: if
		// we used VERTEX, rotating the character would drag the noise across
		// the surface and the line would crawl.
		// The vertex seed scales how much each vertex gets its own noise
		// offset. 0 => whole line moves together; 1 => fully independent.
		vec3 coord = gk_world_pos * jitter_scale;
		coord += vec3(0.0, 0.0, step_index);
		coord += view_normal * jitter_vertex_seed * 3.0;

		// --- 3. two independent noise samples --------------------------------
		// One drives the thickness, a separate channel drives the sideways
		// slide. Using the same value for both makes the motion look mechanical.
		float breathe = gk_value_noise(coord);
		float slide = gk_value_noise(coord + vec3(17.3, 5.1, 9.7));

		// Centre the noise on 0 so the average width is unchanged.
		breathe = breathe * 2.0 - 1.0;
		slide = slide * 2.0 - 1.0;

		width *= 1.0 + breathe * jitter_width;

		// Slide along a view-space tangent so the line shifts sideways rather
		// than in/out of the surface.
		vec3 tangent = normalize(cross(view_normal, to_cam) + vec3(0.0001));
		line_offset = tangent * slide * jitter_offset;
	}

	// Expand along the view-space normal, plus the hand-drawn slide.
	view_pos.xyz += view_normal * width + line_offset;

	// Feed the modified position to the engine.
	POSITION = PROJECTION_MATRIX * view_pos;
}

void fragment() {
	ALBEDO = outline_color.rgb;
	ALPHA = outline_color.a * outline_alpha * step(0.5, outline_only + 0.5);
}
