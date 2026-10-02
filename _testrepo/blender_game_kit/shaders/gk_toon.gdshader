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
//     不少面片是反的;开剔除会整片消失。所以双面都画。
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

		float lit_factor = gk_band(raw, shadow_step, shadow_smooth);
		vec3 ramp = gk_ramp(raw);

		// Anime shadows are a TINT, not a darkening: shadowed areas keep
		// `ambient` of the texture colour, pulled toward shadow_tint. Lit
		// areas get the full banded ramp. Mixing the two by lit_factor keeps
		// the transition exactly as hard as shadow_smooth says -- no ambient
		// wash leaking over the band edge.
		vec3 shadow_col = base.rgb * shadow_tint.rgb;
		vec3 lit_col = base.rgb * ramp * light_color.rgb * light_energy;
		vec3 cel = mix(shadow_col * shadow_keep, lit_col, lit_factor);

		// Floor: never let a texel go completely black. Fully shadowed anime
		// characters are still readable, not silhouettes.
		cel = max(cel, base.rgb * shadow_tint.rgb * shadow_keep * 0.85);
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
	if (specular_amount > 0.001) {
		vec3 n_view = normalize(NORMAL);
		vec3 view_dir = normalize(VIEW);
		vec3 light_view = normalize(mat3(VIEW_MATRIX) * l_world);
		vec3 half_vec = normalize(view_dir + light_view);
		float spot = gk_band(dot(n_view, half_vec), 1.0 - specular_size, specular_smooth);
		EMISSION += specular_color.rgb * spot * specular_amount;
	}

	// --- rim light ---------------------------------------------------------
	// 边缘光同样是**视图空间**(它就是"轮廓"光,随视角变化才是对的)。
	if (rim_amount > 0.001) {
		float nv = dot(normalize(NORMAL), normalize(VIEW));
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
