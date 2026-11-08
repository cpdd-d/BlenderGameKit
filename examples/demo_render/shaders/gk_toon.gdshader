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

// ── render_mode 三个都是**必须的**,不是可选优化 ──────────────────────
//
// **1) unshaded** —— 见上面那段长注释。Godot 自己不做任何光照,
//     整个卡通外观由我们在 fragment() 里算。
//
// **2) depth_draw_opaque** —— 这个是"四维穿透"的**解药**。
//     MMD 模型经 mmd_tools 导入后,导出的 glTF 里**每个材质都是
//     alphaMode=BLEND**(实测:32 个材质全部 BLEND,其中 30 个还是
//     doubleSided)。BLEND 材质在 Godot 里走透明队列:
//     **不写深度、按距离排序**。
//     后果(用户原话):
//       "从前面看,居然能看到后面的头发流向情况"
//       "还有模型内部的穿模显示(前面看到帽子内沿颜色)"
//     因为不写深度,后面的面片会画到前面的面片上面去。
//     我们的 shader 其实是**抠图(cutout)**不是半透明 ——
//     像素要么 discard 要么完全不透明,所以**应该**写深度。
//     显式声明 depth_draw_opaque 把它拉回不透明队列。
//
// **3) cull_disabled** —— MMD 大量用单面片做头发/帽子/裙摆,
//     不少面片是反的;开剔除会整片消失。所以**默认**双面都画。
//     ── 要背面剔除的(封闭模型:方块 / 房子 / 地形)────────────────
//     在 Blender 的材质设置里勾『背面剔除』(Backface Culling)就行 ——
//     导出会给**那个材质**换用剔面版 `gk_toon_cull.gdshader`(它由本文件
//     导出时生成,只把下面那行换成硬件剔背面的版本;本文件一个字都不动)。
//     Blender 预览也认同一个勾,两边一致。
//     ⚠ 单面片模型别勾 —— 那些面片只有一面,剔了会整片消失。
//     (注意:这一条**不是**穿透问题的原因 —— 实测开/关剔除
//      渲染结果字节完全相同,那时真凶是 depth_draw_opaque 没写。)
render_mode unshaded, depth_draw_opaque, cull_disabled;

group_uniforms base;
uniform vec4 albedo_color : source_color = vec4(1.0, 0.932, 0.912, 1.0);
uniform sampler2D albedo_texture : source_color, hint_default_white, filter_linear_mipmap;
// 0 = albedo_color only; 1 = albedo_color * texture.
// Must be explicit: `hint_default_white` makes texture() always return white
// when unset, so the shader cannot tell "no texture" from "a white texture".
uniform float use_albedo_texture : hint_range(0.0, 1.0) = 0.0;
uniform float albedo_strength : hint_range(0.0, 2.0) = 1.0;

group_uniforms light_source;
// (旧的"视图空间"说明已删除 —— 那样做会让阴影跟着相机转,是错的。
//  完整说明见下面 light_dir 那一段。)
// Direction the light comes FROM, in **world space**.
//
// ── 为什么必须是世界空间(踩过的坑)────────────────────────────────
// 一开始这里写的是"视图空间",直接用 fragment() 里现成的 NORMAL/VIEW
// 去点乘。省事,但**阴影会跟着相机转** —— 用户原话:
//   "这阴影上有点错误,不像二次元的效果,感觉它是跟着摄像机与视图角度
//    变化而变化"
// 完全正确。二次元的阴影是"画"在角色身上的,光和角色是**世界**里的
// 固定关系,相机转到哪阴影都该在同一个位置。
// 所以这里收到世界空间,在 fragment() 里把法线也转回世界空间再点乘。
//
// 默认值 = 左上前方(经典动画主光)。
uniform vec3 light_dir = vec3(-0.35, 0.75, 0.55);
uniform vec4 light_color : source_color = vec4(1.0, 0.98, 0.95, 1.0);
// Overall brightness. 1.0 is "as authored"; lower it to sit a character into
// a darker scene without touching the tints.
uniform float light_energy : hint_range(0.0, 4.0) = 1.0;
// How much of the texture shows in fully shadowed areas. Anime shadows are
// tinted, not black, so this stays fairly high.
uniform float ambient : hint_range(0.0, 1.0) = 0.55;

// Second (fill) light. The environment script fills the two strongest lights;
// the fill only ADDS light where it hits, so it never erases the primary
// cel bands (which is what made the old "just add all lights" approach grey).
group_uniforms light_source_2;
uniform vec3 light_dir_2 = vec3(0.0);
uniform vec4 light_color_2 : source_color = vec4(1.0, 0.98, 0.95, 1.0);
uniform float light_energy_2 : hint_range(0.0, 4.0) = 0.0;
// Where the fill light's own terminator sits. 0 = light the whole hemisphere
// facing the fill (the usual choice for a fill); raise it to make the fill
// narrower.
uniform float light_step_2 : hint_range(-1.0, 1.0) = 0.0;

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
// Shape texture (round 101): takes over the *profile* of the highlight, so
// the artist draws it instead of accepting a plain filled disc -- hard core
// + soft edge, a hollow ring (dark middle -> the highlight becomes a rim),
// a crescent... whatever the ramp says.
// Layout: a HORIZONTAL gradient sampled at v = 0.5 (a 1 pixel tall strip is
// enough); x = 0 at the outer edge of the highlight, x = 1 at its centre.
// NOTE the sampled coordinate is CONTINUOUS (raw N.H remapped over the
// highlight's radius), not the binary band value -- sampling a ramp with a
// hard 0/1 term would only ever read the two end pixels and the "shape"
// texture could not draw anything in between. Consequence: once a shape
// texture is used, `specular_smooth` no longer applies -- softness is
// whatever the gradient says.
// `hint_default_white` keeps an unset texture a no-op.
uniform sampler2D specular_shape_texture : hint_default_white, filter_linear, repeat_disable;
uniform float use_specular_shape_texture : hint_range(0.0, 1.0) = 0.0;
// Mask texture (round 101): sampled by UV, decides *where* the highlight is
// allowed to show up (only on the forehead, only on the shoulder plate...).
// White = keep the highlight, black = remove it. This one is a data mask,
// deliberately NOT `source_color` so no sRGB decode happens.
uniform sampler2D specular_mask_texture : hint_default_white, filter_linear, repeat_disable;
uniform float use_specular_mask_texture : hint_range(0.0, 1.0) = 0.0;

group_uniforms rim_light;
uniform vec4 rim_color : source_color = vec4(0.75, 0.85, 1.0, 1.0);
uniform float rim_amount : hint_range(0.0, 4.0) = 0.0;
uniform float rim_width : hint_range(0.0, 1.0) = 0.55;
uniform float rim_front : hint_range(0.0, 1.0) = 0.15;

group_uniforms face_sdf;
// Anime face shading: one clean shadow line looked up from a texture instead
// of the blotchy geometric terminator. The texture is a horizontal strip:
// left half = light from screen-left, right half = its mirror. White = lit,
// black = shadow. Draw it on a second UV map (UV2) in Blender.
uniform sampler2D face_sdf : hint_default_white, filter_linear, repeat_disable;
uniform float face_sdf_mix : hint_range(0.0, 1.0) = 0.0;
uniform float face_sdf_split : hint_range(0.05, 0.95) = 0.5;
uniform float face_sdf_neutral : hint_range(0.0, 1.0) = 0.5;
uniform float face_sdf_angle_scale : hint_range(0.25, 4.0) = 1.6;
uniform float face_sdf_smooth : hint_range(0.001, 0.5) = 0.03;
uniform float face_sdf_v_offset : hint_range(-0.5, 0.5) = 0.0;
// 0 = use UV, 1 = use UV2. Simple face quads have no UV2 at all, and the
// engine's fallback value for a missing UV2 is not something to rely on.
uniform float face_sdf_use_uv2 : hint_range(0.0, 1.0) = 0.0;

group_uniforms misc;
uniform float alpha_cut : hint_range(0.0, 1.0) = 0.5;
uniform float emission_amount : hint_range(0.0, 8.0) = 0.0;
uniform float eye_highlight_boost : hint_range(0.0, 8.0) = 0.0;

// World position, used by the outline pass (next_pass) to keep width constant.
varying vec3 gk_world_pos;
// UV2 is not available in every stage; carry it so the face SDF can use it.
varying vec2 gk_uv2;

// A near-hard step that still antialiases. This is the heart of cel shading:
// a smooth function squeezed into a very narrow band.
float gk_band(float x, float edge, float width) {
	float half_w = max(width, 0.0005) * 0.5;
	return smoothstep(edge - half_w, edge + half_w, x);
}

// Face shadow from the SDF strip. Returns -1 (full shadow) .. 1 (full lit).
// light_view / view_dir are both in view space, so the line slides with the
// light relative to the camera, not with the model's normals.
float gk_face_shadow(vec3 light_view, vec3 view_dir, vec2 base_uv) {
	float side = cross(view_dir, light_view).y;
	// Godot's VIEW points from the camera toward the fragment, so a light
	// BEHIND the head has a positive dot product here. (The old negative
	// version darkened every face to full shadow -- fixed 2026-11-08.)
	float behind = clamp(dot(view_dir, light_view) * 1.5, 0.0, 1.0);
	float amount = clamp(abs(side) * face_sdf_angle_scale, 0.0, 1.0);

	// Line position inside the strip: light from screen-right slides left,
	// light from screen-left slides right.
	float line = face_sdf_neutral;
	if (side >= 0.0) {
		line = mix(face_sdf_neutral, 0.0, amount);
	} else {
		line = mix(face_sdf_neutral, 1.0, amount);
	}

	// Sample the SDF with the face's own UV shifted by (line - neutral).
	// Do NOT replace U with the line position: that samples a single column
	// and the whole face becomes one flat colour (the mistake the Blender
	// preview documents in npr_preview.py).
	vec2 sdf_uv = vec2(base_uv.x + (line - face_sdf_neutral),
		base_uv.y + face_sdf_v_offset);
	float mask = texture(face_sdf, sdf_uv).r;
	mask *= 1.0 - behind;

	float raw = mask * 2.0 - 1.0;
	return gk_band(raw, -0.5 + shadow_smooth, face_sdf_smooth) * 2.0 - 1.0;
}

void vertex() {
	gk_world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	gk_uv2 = UV2;
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
	// ── 一定要在世界空间里点乘(踩过的坑)─────────────────────────
	// Godot 给 fragment() 的 NORMAL 和 VIEW 都是**视图空间**的。
	// 一开始图省事直接 `dot(NORMAL, light_dir)`,结果**阴影跟着相机转**:
	// 转一下视角,脸上的阴影线就跑到别的地方去了。
	// 但二次元的阴影是"画"在角色身上的 —— 光和角色在世界里的关系是
	// 固定的,相机转到哪阴影都该在原地。
	//
	// 所以:用 INV_VIEW_MATRIX 把法线转回**世界空间**,
	// 再和世界空间的 light_dir 点乘。
	//
	// 高光(镜面)和边缘光仍然用视图空间 —— 那两个**本来**就该随视角变化
	// (高光跟着相机走是物理正确的,边缘光就是轮廓光)。
	// ------------------------------------------------------------------
	mat3 to_world = mat3(INV_VIEW_MATRIX);
	vec3 n_world = normalize(to_world * NORMAL);
	vec3 l_world = normalize(light_dir);

	// ── 场景里没有灯时:只画环境色,不画色阶 ──────────────────────
	// gk_environment.gd 在"灯全关了"时会把 light_dir 写成零向量。
	// 不特判的话 normalize(零向量) 会出 NaN,整块面片变成黑块或花屏。
	// 语义上这也对:没有光就没有明暗分界,只剩环境色。
	//
	// 注意:**不能用 return** —— Godot 的 fragment() 不允许提前返回
	// (实测报 "'return' 在 'fragment' 处理器函数中的使用不正确")。
	// 所以用 if/else 把两条路分开。
	float shadow_keep = clamp(ambient, 0.0, 1.0);
	if (dot(light_dir, light_dir) < 1e-6) {
		ALBEDO = max(base.rgb * shadow_tint.rgb * shadow_keep, base.rgb * 0.15);
	} else {
		// A plain dot keeps the terminator crisp, which is the whole point of
		// a cel look. (A half-lambert wrap would smear it out.)
		float raw = dot(n_world, l_world);
		if (face_sdf_mix > 0.001) {
			vec3 light_view = normalize(mat3(VIEW_MATRIX) * l_world);
			vec2 base_uv = face_sdf_use_uv2 > 0.5 ? gk_uv2 : UV;
			float face = gk_face_shadow(light_view, normalize(VIEW), base_uv);
			raw = mix(raw, face, face_sdf_mix);
		}

		float lit_factor = gk_band(raw, shadow_step, shadow_smooth);
		vec3 shadow_col = base.rgb * shadow_tint.rgb * shadow_keep;
		vec3 lit_col = base.rgb * lit_tint.rgb * light_color.rgb * light_energy;

		// ── Real 2 / 3 band shading ──────────────────────────────────
		// The previous version mixed in gk_ramp() but then used lit_factor
		// to pick between shadow_col and lit_col. Because lit_factor is
		// 0 in the whole shadow region, the middle band was never selected:
		// "three_tone" produced exactly the same two bands as two-tone.
		// Here every band is its own colour, chosen by its own threshold:
		//   raw < shadow_step_2  -> shadow_tint_2 (deepest)
		//   shadow_step_2 .. shadow_step -> shadow_tint
		//   raw > shadow_step    -> lit_tint * light
		vec3 cel;
		if (three_tone > 0.5) {
			// Keep the second split strictly below the first one. If an artist
			// sets shadow_step_2 above shadow_step, the "deepest" band would
			// cover the lit area and the lit colour would disappear entirely.
			float step2 = min(shadow_step_2, shadow_step - 0.001);
			float deep_factor = gk_band(raw, step2, shadow_smooth);
			vec3 deep_col = base.rgb * shadow_tint_2.rgb * shadow_keep;
			cel = mix(deep_col, mix(shadow_col, lit_col, lit_factor), deep_factor);
		} else {
			cel = mix(shadow_col, lit_col, lit_factor);
		}

		// The floor tint must follow the active mode: in two-tone the shadow
		// colour is shadow_tint, so using shadow_tint_2 here would let a
		// brighter deep-shadow colour leak into the two-tone look.
		vec3 floor_tint = three_tone > 0.5 ? shadow_tint_2.rgb : shadow_tint.rgb;

		// ── Second light: fill only, never darken ────────────────────
		// The primary light owns the cel bands. A real scene's fill is usually
		// dimmer than the cel shadow floor (0.15-0.3 vs ~0.4), so
		// `max(cel, base * energy)` would let the shadow swallow it. The fill
		// therefore ADDS to the deepest tone of the active mode: a 0.25 fill
		// lifts a 0.4 shadow to ~0.65, while lit areas (already brighter) are
		// left untouched by the outer max().
		if (dot(light_dir_2, light_dir_2) > 1e-6 && light_energy_2 > 0.001) {
			vec3 l2 = normalize(light_dir_2);
			float raw2 = dot(n_world, l2);
			if (face_sdf_mix > 0.001) {
				vec3 light_view_2 = normalize(mat3(VIEW_MATRIX) * l2);
				vec2 base_uv_2 = face_sdf_use_uv2 > 0.5 ? gk_uv2 : UV;
				float face2 = gk_face_shadow(light_view_2, normalize(VIEW), base_uv_2);
				raw2 = mix(raw2, face2, face_sdf_mix);
			}
			float lit_2 = gk_band(raw2, light_step_2, shadow_smooth);
			vec3 fill_lit = base.rgb * (floor_tint * shadow_keep
				+ lit_tint.rgb * light_color_2.rgb * light_energy_2);
			cel = mix(cel, max(cel, fill_lit), lit_2);
		}

		// Floor: never let a texel go completely black. Fully shadowed anime
		// characters are still readable, not silhouettes.
		cel = max(cel, base.rgb * floor_tint * shadow_keep * 0.85);
		ALBEDO = cel;
	}
	// ── 绝对不要在这里写 ALPHA ────────────────────────────────────────
	// 这是"四维穿透"的最后一块拼图。
	//
	// Godot 只要在 fragment() 里看到对 `ALPHA` 的**赋值**,就会给这个
	// shader 生成 alpha varying,并把它归到**透明**管线 ——
	// 于是不写深度、按距离排序,后面的面片画到前面去。
	// 实测:写 `ALPHA = 1.0` 也照样进透明队列
	// (值是多少不重要,**写过**才重要)。
	//
	// 我们的 shader 是**抠图(cutout)**:像素要么被 discard 丢掉,
	// 要么完全不透明 —— 根本不需要 alpha 通道。
	// 不写 ALPHA,Godot 就当它是普通不透明材质,正常写深度。
	// 配合 `render_mode depth_draw_opaque` 双保险。
	//
	// 踩过的历史:以前这里写 `ALPHA = alpha`(真半透明),
	// 用户报"左右移动时模型一闪一闪" —— 那是透明排序抖动。
	// 改成 `ALPHA = 1.0` 治好了闪烁,但**没治穿透**;
	// 直到现在完全不写 ALPHA 才对。
	METALLIC = 0.0;
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;

	// --- cel highlight: a hard-edged spot instead of a soft falloff ---------
	// 高光**故意**留在视图空间:高光本来就该跟着相机走(物理上就是
	// 视线与光线的半程向量),这跟阴影"必须固定在世界里"不矛盾。
	//
	// ⚠⚠ 这里以及下面几段必须写 **ALBEDO**,不能写 EMISSION(第 101 轮实测)。
	//
	// 本 shader 是 `unshaded` —— 真机标定(一颗深色球 + specular_amount 1.6,
	// 真窗口出图数亮点):
	//     写 EMISSION:峰值 0.114(就是底色本身,高光**一点都没有**)
	//     写 ALBEDO  :峰值 1.000,实心高光 20880 个亮点
	// 也就是说 **unshaded 下 Godot 根本不看 EMISSION**(unshaded = 跳过整个
	// 光照/自发光阶段,直接拿 ALBEDO 当最终颜色)。这意味着在这之前,
	// 高光 / 边缘光 / 自发光 / 眼睛高光**四个参数全是死的** ——
	// 面板上调半天画面纹丝不动。改成 ALBEDO 之后它们才真的生效。
	if (specular_amount > 0.001) {
		vec3 n_view = normalize(NORMAL);
		vec3 view_dir = normalize(VIEW);
		vec3 light_view = normalize(mat3(VIEW_MATRIX) * l_world);
		vec3 half_vec = normalize(view_dir + light_view);
		float half_spot = dot(n_view, half_vec);
		float spot = gk_band(half_spot, 1.0 - specular_size, specular_smooth);
		// Round 101: let a texture reshape the highlight. Shape first (it
		// rewrites the whole profile), then the UV mask (it decides where
		// the highlight is allowed to appear at all).
		if (use_specular_shape_texture > 0.5) {
			float u = clamp((half_spot - (1.0 - specular_size))
				/ max(specular_size, 0.0001), 0.0, 1.0);
			spot = texture(specular_shape_texture, vec2(u, 0.5)).r;
		}
		if (use_specular_mask_texture > 0.5) {
			spot *= texture(specular_mask_texture, UV).r;
		}
		ALBEDO += specular_color.rgb * spot * specular_amount;
	}

	// --- rim light ---------------------------------------------------------
	// 边缘光同样是**视图空间**(它就是"轮廓"光,随视角变化才是对的)。
	if (rim_amount > 0.001) {
		float nv = dot(normalize(NORMAL), normalize(VIEW));
		float rim = 1.0 - smoothstep(rim_width, 1.0, nv);
		rim = clamp(rim + rim_front * (1.0 - nv), 0.0, 1.0);
		ALBEDO += rim_color.rgb * rim * rim_amount;
	}

	if (emission_amount > 0.001) {
		ALBEDO += base.rgb * emission_amount;
	}
	if (eye_highlight_boost > 0.001) {
		// Bright pixels (eye highlights painted into the texture) glow
		// slightly, which makes the eyes read at small sizes.
		float bright = smoothstep(0.75, 1.0, dot(base.rgb, vec3(0.333)));
		ALBEDO += base.rgb * bright * eye_highlight_boost;
	}
}
