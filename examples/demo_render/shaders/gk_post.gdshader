// 游戏环境与后期 —— 全屏后期着色器(第一百二十五轮)
//
// 一张全屏 ColorRect 上挂这个 shader,同时管三件事:
//   1. 色调(亮度 / 对比度 / 饱和度 / 色调 + 色调量)
//   2. 屏幕位置(圆形 / 矩形蒙版 —— 只在指定区域调色)
//   3. 睁眼效果(上下眼皮张开:中间一条横缝 -> 向上下张到全屏)
//
// ── 为什么三件事合成一个 shader ───────────────────────────────────
// 分开做要三张全屏 ColorRect,每张都要读一次屏幕纹理 —— 既费带宽,
// 又会出现"谁先画"的顺序问题(顺序一错,眼皮就盖不住调色)。
// 合成一张之后顺序天然正确:先调色,再蒙版,最后眼皮盖上去。
//
// ⚠ 读屏幕纹理(`hint_screen_texture`)会把这一帧已经画好的画面取回来,
//   所以这个 ColorRect 必须画在 3D 场景**之后** —— 生成侧把它放进
//   layer 最高的 CanvasLayer 里(见 gk_environment 脚本的 _gk_post_ready)。
shader_type canvas_item;
render_mode unshaded;

// 已经画好的画面
uniform sampler2D screen_tex : hint_screen_texture, filter_linear_mipmap;

// ── 色调(1 = 不动)────────────────────────────────────────────────
uniform float grade_on = 0.0;
uniform float brightness = 1.0;
uniform float contrast = 1.0;
uniform float saturation = 1.0;
uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform float tint_amount = 0.0;

// ── 屏幕位置(蒙版)────────────────────────────────────────────────
// mask_on = 0 时整屏统一调色;= 1 时只作用在蒙版内(或外,看 mask_invert)
uniform float mask_on = 0.0;
uniform float mask_round = 1.0;    // 1 = 圆形(用 radius),0 = 矩形(用 size)
uniform vec2 mask_center = vec2(0.5, 0.5);
uniform float mask_radius = 0.35;
uniform vec2 mask_size = vec2(0.5, 0.4);
uniform float mask_feather = 0.1;
uniform float mask_invert = 0.0;
uniform float viewport_aspect = 1.7777778;

// ── 睁眼效果 ──────────────────────────────────────────────────────
// eye_amount:0 = 完全闭合(整屏都是眼皮颜色),1 = 完全张开(看不见眼皮)
uniform float eye_on = 0.0;
uniform float eye_amount = 1.0;
uniform vec4 eye_color : source_color = vec4(0.0, 0.0, 0.0, 1.0);

void fragment() {
	vec3 src = texture(screen_tex, SCREEN_UV).rgb;
	vec3 col = src;

	if (grade_on > 0.5) {
		// 亮度 -> 对比度 -> 饱和度 -> 色调,和 Godot 的 Environment
		// adjustment_* 同一套顺序(那边也是先亮度再对比最后饱和)
		vec3 g = src * brightness;
		g = (g - vec3(0.5)) * contrast + vec3(0.5);
		float luma = dot(g, vec3(0.2126, 0.7152, 0.0722));
		g = mix(vec3(luma), g, saturation);
		g = mix(g, g * tint.rgb, tint_amount);

		// 蒙版权重:1 = 这块像素要调色,0 = 保持原样
		float weight = 1.0;
		if (mask_on > 0.5) {
			// 中心与距离都按"屏幕宽高比"归一化,圆形才不会被拉成椭圆
			vec2 d = (SCREEN_UV - mask_center) * vec2(viewport_aspect, 1.0);
			float dist;
			if (mask_round > 0.5) {
				dist = length(d);
			} else {
				vec2 half_size = mask_size * 0.5 * vec2(viewport_aspect, 1.0);
				vec2 q = abs(d) - half_size;
				dist = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0);
			}
			float edge = max(mask_feather, 0.0001);
			// 圆形:dist 直接和半径比;矩形:上面算的是"到矩形边界的有符号距离"
			float inside = (mask_round > 0.5)
				? 1.0 - smoothstep(mask_radius - edge, mask_radius + edge, dist)
				: 1.0 - smoothstep(-edge, edge, dist);
			weight = mix(inside, 1.0 - inside, mask_invert);
		}
		col = mix(src, g, weight);
	}

	if (eye_on > 0.5) {
		// 眼皮:以屏幕中线为中心的一条缝。eye_amount = 1 时缝比屏幕还高
		// (整屏都不盖),= 0 时缝高为 0(整屏盖住)。
		float half_open = eye_amount * 0.5;
		float d = abs(SCREEN_UV.y - 0.5);
		// 边缘留一点点过渡,否则张开时会看到一条硬锯齿线
		float lid = smoothstep(half_open - 0.004, half_open + 0.004, d);
		col = mix(col, eye_color.rgb, lid);
	}

	COLOR = vec4(col, 1.0);
}
