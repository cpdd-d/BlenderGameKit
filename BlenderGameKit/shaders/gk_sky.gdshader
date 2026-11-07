// 游戏环境与后期 —— 昼夜天空盒 + 流动云层(第一百三十三轮)
//
// 一张 `shader_type sky` 的着色器,挂到 Godot 的 `Sky.sky_material` 上
// (WorldEnvironment -> Environment.sky -> Sky -> ShaderMaterial)。
//
// ── 为什么是**程序化**噪声,不是一张贴图 ────────────────────────────
// 用户要的是"随着时间流逝,从卡通日间到卡通夜晚"+ 云层。贴图方案要么
// 得随包带好几张 (日/夜/黄昏 × 有云/无云),要么得运行时烘焙 —— 前者
// 包体翻倍,后者在卡通风格下完全没必要。程序化噪声只有一个文件,
// 而且**任何时刻**的天色都是连续插值出来的。
//
// ── 谁在动这些参数 ──────────────────────────────────────────────────
// `gk_environment.gd` 里的昼夜循环驱动块(`asset_export._daynight_block`):
//   * 每个物理帧按『一天多少秒』推进时刻,在关键帧之间插值;
//   * 把插值结果写进这些 uniform(天色 / 太阳 / 云量 / 云速);
//   * 同时把场景里那盏平行光**真的转过去**(影子方向跟着走)。
// 也就是说:这个 shader 只管"画",时间轴在 GDScript 那边(用户可以在
// 逻辑图里暂停 / 加速 / 跳到某个时刻)。
//
// ⚠ 老实说清楚(也写进了 godot_look.describe):Blender 预览只能按
//   "当前时刻铺个渐变色"近似,**画不出**逐像素的噪声云 —— 预览里的天空
//   和游戏里不会一模一样。这不是没做,是 Blender 视口里塞不进一套
//   Godot 的 sky shader(见 README 里"为什么不做 Godot 渲染引擎"那段)。
shader_type sky;

// ── 天色(昼夜循环每帧插值后写进来)──────────────────────────────────
uniform vec4 sky_top : source_color = vec4(0.25, 0.45, 0.85, 1.0);
uniform vec4 sky_horizon : source_color = vec4(0.72, 0.82, 0.95, 1.0);
uniform vec4 ground_color : source_color = vec4(0.12, 0.12, 0.14, 1.0);
// 0 = 全黑(夜),1 = 全亮(昼)。夜里连太阳盘一起压暗。
uniform float day_amount : hint_range(0.0, 1.0) = 1.0;
// 地平线那圈"霞光"的强度(黄昏/清晨把它拧大就有橙红色的边)
uniform float horizon_glow : hint_range(0.0, 2.0) = 0.0;
uniform vec4 horizon_glow_color : source_color = vec4(1.0, 0.55, 0.28, 1.0);

// ── 太阳 ────────────────────────────────────────────────────────────
// ⚠ 用**自己传进来的**方向,不读 `LIGHT0_DIRECTION`:平行光可以有好多盏,
//   而"这一关的主光"是导出侧定的(和卡通材质的 light_dir 同一盏)。
uniform vec3 sun_dir = vec3(0.0, 1.0, 0.0);
uniform vec4 sun_color : source_color = vec4(1.0, 0.96, 0.88, 1.0);
uniform float sun_energy : hint_range(0.0, 8.0) = 1.0;
// 太阳盘的**角半径(弧度)**,不是"点积阈值"。
// 旧写法把它当 dot 阈值用:0.06 会得到 20° 半径的大圆盘,
// 同时把月盘也放大成 13° 半径 —— 实机里月亮占满半个窗口。
uniform float sun_size : hint_range(0.0, 1.2) = 0.06;

// ── 星星 ────────────────────────────────────────────────────────────
// 球面网格 + 哈希散点,不用贴图。只在天空够暗时出现(白天 / 黄昏自动隐去)。
uniform float star_amount : hint_range(0.0, 1.0) = 0.7;
uniform float star_brightness : hint_range(0.0, 4.0) = 1.0;
// 星星闪动强度(第一百五十四轮):0 = 恒定亮着,1 = 正常闪烁,2 = 很夸张。
// 旧版只有 0.70~1.00 的轻微呼吸,实机里几乎看不出来(用户要"星星闪动")。
uniform float star_twinkle : hint_range(0.0, 2.0) = 1.0;
// 南北半球(第一百五十二轮):0 = 北半球,1 = 南半球。
// 两个半球的星空不是"同一片天转个角度" —— 观感差异有三处:
//   * 北天有**北极星**(几乎不动,全天星星绕着它转);
//   * 南天看不到北极星,取而代之的是低空两团**麦哲伦云**;
//   * 星空的赤纬方向反过来(南天星图是北天的镜像)。
// 所以这里不只是换个哈希种子,而是真的换一套星星布局。
uniform float star_hemisphere : hint_range(0.0, 1.0) = 0.0;

// ── 云层 ────────────────────────────────────────────────────────────
uniform float cloud_on : hint_range(0.0, 1.0) = 0.0;
uniform float cloud_amount : hint_range(0.0, 1.0) = 0.45;
uniform float cloud_scale = 3.0;
uniform float cloud_speed = 0.06;
// 云底压在地平线以上多少(以 sin(仰角) 计;0.06 ≈ 3.4°)
uniform float cloud_height : hint_range(0.0, 0.6) = 0.06;
uniform float cloud_softness : hint_range(0.01, 0.6) = 0.22;
uniform vec4 cloud_light : source_color = vec4(1.0, 0.98, 0.95, 1.0);
uniform vec4 cloud_dark : source_color = vec4(0.45, 0.48, 0.58, 1.0);
// 云密度贴图(第一百五十一轮第四轮):导出时用 CPU 烘好的**无缝**噪声,
// 运行时只采样。逐像素程序化噪声在部分 GPU 上会量化成"方形云块";
// 贴图方案不受驱动精度影响,形状也由 CPU 决定。没有贴图时退回程序化噪声。
uniform sampler2D cloud_texture : hint_default_white, filter_linear, repeat_enable;
uniform float use_cloud_texture : hint_range(0.0, 1.0) = 0.0;
// 由 GDScript 累加(不是引擎的 TIME:`TIME` 在暂停时也走,而我们要能暂停)
uniform float time_offset = 0.0;

// ── 月亮(第一百四十二轮)───────────────────────────────────────────
// 由环境脚本按『环境_月亮』卡每帧写入:方向 / 颜色 / 盘大小 / 亮度 / 月相。
uniform vec3 moon_dir = vec3(0.0, -1.0, 0.0);
uniform vec4 moon_color : source_color = vec4(0.80, 0.86, 1.0, 1.0);
// 月盘的**角半径(弧度)**:0.06 ≈ 3.4° 半径 / 6.9° 直径。
uniform float moon_size : hint_range(0.0, 1.2) = 0.06;
uniform float moon_energy : hint_range(0.0, 8.0) = 0.0;
// 月相:1 = 满月,0 = 新月。晨昏线按椭圆算,弯月是自然的圆弧,不是竖线。
uniform float moon_phase : hint_range(0.0, 1.0) = 1.0;
// 月面纹理(第一百五十一轮):用一张程序生成的月海 / 环形山贴图,
// 让月亮不再是"光秃秃的一个圆点"。没有纹理时自动退回纯色月盘。
uniform sampler2D moon_texture : hint_default_white, filter_linear, repeat_disable;
uniform float use_moon_texture : hint_range(0.0, 1.0) = 0.0;


// ── 值噪声(不用贴图,所以必须自己写)───────────────────────────────
// 两套不同的哈希混一遍,避免规则网格的条纹被人眼一眼看出来。
float gk_hash(vec2 p) {
	vec2 q = fract(p * vec2(123.34, 456.21));
	q += dot(q, q + 45.32);
	return fract(q.x * q.y);
}

float gk_value_noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	// 平滑插值(五次多项式),比 smoothstep 少一点方块感
	vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
	float a = gk_hash(i);
	float b = gk_hash(i + vec2(1.0, 0.0));
	float c = gk_hash(i + vec2(0.0, 1.0));
	float d = gk_hash(i + vec2(1.0, 1.0));
	return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

// 四个倍频就够了:卡通云要的是"大团",不是写实云的细节
float gk_cloud_density(vec2 p) {
	float total = 0.0;
	float amplitude = 0.5;
	float scale = 1.0;
	for (int i = 0; i < 4; i++) {
		total += gk_value_noise(p * scale) * amplitude;
		scale *= 2.03;
		amplitude *= 0.5;
	}
	return total;
}

void sky() {
	vec3 dir = normalize(EYEDIR);
	float up = clamp(dir.y, -1.0, 1.0);

	// ── 天空底色:地平线 -> 天顶 的渐变 ──────────────────────────
	vec3 color = mix(sky_horizon.rgb, sky_top.rgb,
			smoothstep(-0.05, 0.65, up));
	// 地平线那圈霞光(黄昏/清晨的大橙边)
	if (horizon_glow > 0.001) {
		float band = 1.0 - smoothstep(0.0, 0.28, abs(up));
		color = mix(color, horizon_glow_color.rgb,
				clamp(band * horizon_glow, 0.0, 1.0));
	}
	// 地平线以下:地面那半边(免得"地面颜色"漏到天空中)
	if (up < 0.0) {
		color = mix(sky_horizon.rgb, ground_color.rgb,
				smoothstep(0.0, -0.3, up));
	}

	// ── 星星 ──────────────────────────────────────────────────────
	// 天空够暗才出现:用天空色的亮度判断,不依赖 day_amount ——
	// 没接昼夜循环的"固定夜晚"场景(恐怖示例)也能看到星星。
	float sky_lum = dot(sky_horizon.rgb, vec3(0.30, 0.59, 0.11));
	float night = 1.0 - smoothstep(0.08, 0.30, sky_lum);
	if (star_amount > 0.001 && night > 0.001 && up > 0.0) {
		vec2 suv = vec2(atan(dir.z, dir.x) / TAU + 0.5,
				asin(up) / PI + 0.5);
		// ── 南半球:星图镜像 + 方位转半圈 ────────────────────────
		// 赤纬翻过来(南天的星星分布和北天不是同一套),同时让"银河
		// 走向"换一个方向 —— 只翻 y 会让星空看起来像倒过来贴的壁纸,
		// 两个一起翻才像"换了个半球"。
		if (star_hemisphere > 0.5) {
			suv = vec2(fract(suv.x + 0.5), 1.0 - suv.y);
		}
		vec2 sp = suv * vec2(160.0, 80.0);
		vec2 cell = floor(sp);
		vec2 f = fract(sp) - 0.5;
		float h = gk_hash(cell + vec2(4.3, 9.1));
		float keep = star_amount * 0.07;
		if (h > 1.0 - keep) {
			vec2 offset = vec2(gk_hash(cell + vec2(17.1, 3.7)),
					gk_hash(cell + vec2(31.7, 12.9))) - 0.5;
			float d = length(f - offset * 0.75);
			// 星点半径要小:一格约 2.25°,0.025~0.06 格 = 1~2 像素。
			// 旧版 0.08~0.20 格在实机里是一颗颗"雪球"。
			float size = mix(0.025, 0.06, gk_hash(cell + vec2(8.9, 21.4)));
			// 每个星的相位/频率都由哈希决定,不会"整片一起呼吸"。
			float phase = h * 6.2831;
			float tw1 = 0.5 + 0.5 * sin(time_offset * (1.2 + h * 3.5) + phase);
			// 第二层是偶发的"强闪":大部分时间贴 0,偶尔跳一下 ——
			// 真实星空就是这种不规则的眨眼,单一正弦太机械。
			float tw2 = smoothstep(0.86, 1.0,
					sin(time_offset * (0.55 + h * 0.8) + phase * 1.7));
			float twinkle = 1.0 - star_twinkle * (0.42 * (1.0 - tw1)
					- 0.55 * tw2);
			twinkle = max(twinkle, 0.08);
			float s = smoothstep(size, size * 0.20, d) * twinkle;
			s *= smoothstep(0.0, 0.10, up);
			color += vec3(0.78, 0.86, 1.0) * s * star_brightness * night;
		}
	}
	// ── 北天标志:北极星(第一百五十二轮)────────────────────────
	// 位置固定在天顶偏北一点点;它**不参与闪烁**,亮度也更稳 ——
	// 这是北半球最好认的一颗星,也是"我在北半球"的视觉证据。
	if (star_hemisphere < 0.5 && star_amount > 0.001 && night > 0.001) {
		float polar_ang = acos(clamp(dir.y, -1.0, 1.0));
		float polaris = 1.0 - smoothstep(0.0020, 0.0055, polar_ang);
		color += vec3(0.86, 0.92, 1.0) * polaris * 1.6
				* star_brightness * night;
	}
	// ── 南天标志:大 / 小麦哲伦云(第一百五十二轮)──────────────
	// 南半球肉眼可见的两团"云状星系",北半球看不到。放在南偏东低空,
	// 用大范围柔光 + 轻微噪声,不是两个规整的圆。
	if (star_hemisphere > 0.5 && star_amount > 0.001 && night > 0.001) {
		vec3 lmc = normalize(vec3(0.42, 0.10, 0.90));
		vec3 smc = normalize(vec3(0.86, 0.04, 0.50));
		float a1 = acos(clamp(dot(dir, lmc), -1.0, 1.0));
		float a2 = acos(clamp(dot(dir, smc), -1.0, 1.0));
		float n1 = gk_value_noise(vec2(dir.x, dir.z) * 22.0 + 7.0);
		float n2 = gk_value_noise(vec2(dir.x, dir.z) * 28.0 + 31.0);
		color += vec3(0.56, 0.62, 0.86)
				* (1.0 - smoothstep(0.035, 0.150, a1)) * (0.035 + n1 * 0.030)
				* star_brightness * night;
		color += vec3(0.52, 0.58, 0.82)
				* (1.0 - smoothstep(0.020, 0.090, a2)) * (0.028 + n2 * 0.022)
				* star_brightness * night;
	}

	// ── 太阳盘 ──────────────────────────────────────────────────
	// 边缘一圈柔和的溢出,免得小圆盘在低分辨率下闪。
	// ⚠ sun_size = 0 表示"这一关不画太阳":一定要**整段跳过** ——
	//   smoothstep(1,1,x) 在 GLSL 里是未定义行为,实机表现为整片天空
	//   被太阳色洗成暖灰(用户报的"月亮颜色渲染有问题")。
	//   云层也要用 to_sun,所以在块外先声明。
	float to_sun = 0.0;
	float sun_near = 0.0;
	float sun_draw = 0.0;
	if (sun_size > 0.0005) {
		to_sun = dot(dir, normalize(sun_dir));
		float sun_ang = acos(clamp(to_sun, -1.0, 1.0));
		float disc = 1.0 - smoothstep(sun_size * 0.90, sun_size, sun_ang);
		// 太阳不能是一枚"白月亮":近圈暖黄、远圈橙色,云也跟着被烘暖。
		float halo = (1.0 - smoothstep(sun_size * 0.95, sun_size * 4.8, sun_ang))
				* 0.26;
		float wide = 1.0 - smoothstep(sun_size * 2.0, sun_size * 12.0, sun_ang);
		float sun_on_amount = mix(0.15, 1.0, day_amount);
		sun_draw = sun_energy * sun_on_amount;
		color += sun_color.rgb * disc * sun_draw;
		color += vec3(1.0, 0.80, 0.46) * halo * sun_draw;
		color += vec3(1.0, 0.62, 0.28) * wide * 0.20 * sun_draw;
		sun_near = wide;
	}

	// ── 月亮盘(和太阳盘同一条思路,再叠一个月相遮罩)──────────────
	if (moon_energy > 0.001) {
		vec3 mdir = normalize(moon_dir);
		float to_moon = dot(dir, mdir);
		float moon_ang = acos(clamp(to_moon, -1.0, 1.0));
		// ⚠ 盘半径按**角度**算。旧写法把 moon_size 当 dot 阈值,
		//   0.026 会得到 13° 半径的大圆盘(实机里占满半个窗口)。
		float limb = max(moon_size, 0.0015);
		float mdisc = 1.0 - smoothstep(limb * 0.93, limb, moon_ang);
		// 两层光晕:近的一圈稍亮,远的一圈很淡 —— 月亮不会像贴上去的硬圆片。
		float mhalo = (1.0 - smoothstep(limb, limb * 2.2, moon_ang)) * 0.07
				+ (1.0 - smoothstep(limb * 2.2, limb * 5.2, moon_ang)) * 0.025;
		// 月亮自身的切平面坐标:圆盘内 -1..1,纹理不会随视角滑动。
		vec3 up_ref = abs(mdir.y) > 0.99 ? vec3(1.0, 0.0, 0.0)
				: vec3(0.0, 1.0, 0.0);
		vec3 mright = normalize(cross(up_ref, mdir));
		vec3 mup = cross(mdir, mright);
		float sin_limb = max(sin(limb), 0.0001);
		vec2 muv = vec2(dot(dir, mright), dot(dir, mup))
				/ (2.0 * sin_limb) + 0.5;
		float mx = clamp((muv.x - 0.5) * 2.0, -1.0, 1.0);
		float my = clamp((muv.y - 0.5) * 2.0, -1.0, 1.0);
		// 月相:晨昏线是**椭圆**。phase = 0.5 是一条竖线,1 落到左边缘
		// (满月),0 落到右边缘(新月)。弯月的内缘是圆弧,不是直线。
		float k = cos(moon_phase * PI);
		float term = k * sqrt(max(1.0 - my * my, 0.0));
		float lit = smoothstep(term - 0.045, term + 0.045, mx);
		vec3 mtex = mix(vec3(1.0),
				texture(moon_texture, muv).rgb, use_moon_texture);
		// 低空月盘偏暖(大气感),贴近地平线时也稍暗 —— 月升月落不再"贴片"
		float moon_low = smoothstep(-0.04, 0.22, mdir.y);
		vec3 moon_rgb = mix(vec3(1.0, 0.78, 0.56), moon_color.rgb, moon_low);
		float moon_vis = mix(0.55, 1.0, moon_low);
		// 0.012 的地球照:暗面几乎看不见,只留一点点"这不是一块黑饼"的暗示。
		// 0.045 在实机里会被泛光放大成一整块灰圆盘(用户:"月亮形状不合理")。
		color += moon_rgb * mdisc * mtex
				* (lit + 0.012 * (1.0 - lit)) * moon_energy * moon_vis;
		color += moon_rgb * mhalo * moon_energy * moon_vis;
	}

	// ── 云层:球面 UV ────────────────────────────────────────────
	// 旧写法 `dir.xz / up` 在地平线附近把噪声拉成一条条"碎叶子"
	// (用户截图里满天都是小黑点)。改成按方位角 / 仰角采样:
	// 云团是稳定的团块,靠近地平线只是被压扁,不会被拉成高频噪声。
	if (cloud_on > 0.5 && up > cloud_height) {
		vec2 sky_uv = vec2(atan(dir.z, dir.x) / TAU, asin(up) / PI);
		// 天顶:球面 UV 的所有方位都收敛到一点,云被拉成"放射状长条"
		// (用户截图:抬头看像一朵菊花 —— 户外游戏里非常穿帮)。
		// 高仰角改用**俯视平面投影**,两套采样按密度混合;
		// 不混 UV —— 混 UV 会让云形整体糊掉。
		float zenith = smoothstep(0.55, 0.90, up);
		vec2 top_uv = dir.xz / max(up, 0.35);
		float wind = time_offset * cloud_speed;
		float density;
		float density2;
		if (use_cloud_texture > 0.5) {
			// ⚠ 方位角方向的贴图**必须取整数张**:`atan` 在 ±π 处从
			//   -0.5 跳到 +0.5,如果乘的是 2.8 这种非整数,接缝两边的
			//   云图案会差 0.8 张 —— 实机里就是"云被一刀竖直切断"。
			float tiles_x = max(1.0, floor(cloud_scale * 2.0 + 0.5));
			float tiles_x2 = max(1.0, floor(cloud_scale * 3.0 + 0.5));
			float top_tiles = max(1.0, floor(cloud_scale * 0.7 + 0.5));
			float sphere1 = texture(cloud_texture,
					vec2(sky_uv.x * tiles_x + wind,
						sky_uv.y * (cloud_scale * 2.0)
						+ wind * 0.35)).r;
			float sphere2 = texture(cloud_texture,
					vec2(sky_uv.x * tiles_x2 + wind * 0.55 + 0.37,
						sky_uv.y * (cloud_scale * 3.0)
						+ wind * 0.20 + 0.19)).r;
			float top1 = texture(cloud_texture,
					top_uv * top_tiles + vec2(wind, wind * 0.35)).r;
			float top2 = texture(cloud_texture,
					top_uv * (top_tiles * 1.5)
					+ vec2(wind * 0.55 + 0.37, wind * 0.20 + 0.19)).r;
			density = mix(sphere1, top1, zenith);
			density2 = mix(sphere2, top2, zenith);
		} else {
			vec2 plane = sky_uv * (cloud_scale * TAU)
					+ vec2(wind, wind * 0.35);
			float sphere1 = gk_cloud_density(plane);
			vec2 plane2 = sky_uv * (cloud_scale * TAU * 1.75)
					+ vec2(wind * 0.55, wind * 0.20) + vec2(11.3, 7.1);
			float sphere2 = gk_cloud_density(plane2);
			vec2 top_plane = top_uv * (cloud_scale * 2.0)
					+ vec2(wind, wind * 0.35);
			vec2 top_plane2 = top_uv * (cloud_scale * 3.0)
					+ vec2(wind * 0.55, wind * 0.20) + vec2(11.3, 7.1);
			density = mix(sphere1, gk_cloud_density(top_plane), zenith);
			density2 = mix(sphere2, gk_cloud_density(top_plane2), zenith);
		}
		float cover = smoothstep(1.0 - cloud_amount,
				1.0 - cloud_amount + cloud_softness, density);
		float cover2 = smoothstep(1.0 - cloud_amount * 0.8,
				1.0 - cloud_amount * 0.8 + cloud_softness, density2);
		cover = max(cover, cover2 * 0.55);
		// 云被太阳 / 月亮照亮的那一侧偏亮(和月盘同一个点乘)
		float moon_lit = clamp(dot(dir, normalize(moon_dir)) * 0.5 + 0.5,
				0.0, 1.0);
		float lit = mix(clamp(to_sun * 0.5 + 0.5, 0.0, 1.0), moon_lit,
				clamp(moon_energy, 0.0, 1.0) * 0.55);
		vec3 cloud_color = mix(cloud_dark.rgb, cloud_light.rgb, lit);
		// 太阳周围的云被烘成暖黄 —— 白圆盘 + 冷蓝云只会读成"早上的月亮"
		cloud_color = mix(cloud_color, vec3(1.0, 0.76, 0.38),
				clamp(sun_near * day_amount, 0.0, 1.0) * 0.72);
		// 夜里压暗(0.45,不是 0.22 —— 0.22 在实机里会把夜云压成几块黑斑;
		// 白天云量增加 + 云颜色调亮之后,0.45 的夜景正好回到原来的"乌云")
		cloud_color *= mix(0.45, 1.0, day_amount);
		// 月亮附近给云一层银边 —— 云飘过月面时才有"被照亮"的感觉
		float moon_rim = smoothstep(0.72, 1.0,
				dot(dir, normalize(moon_dir))) * 0.5;
		// 靠近地平线的云薄一点(否则远处的云会糊成一条硬边;
		// 0.30 的过渡带让云脚在 3°~20° 之间慢慢消失,而不是一刀切)
		float fade = smoothstep(cloud_height, cloud_height + 0.30, up);
		color = mix(color, cloud_color, cover * cloud_on * fade);
		color += moon_color.rgb * moon_rim * cover * cloud_on * fade
				* clamp(moon_energy, 0.0, 1.0) * 0.35;
	}

	// ── 云之后补一道日核 ────────────────────────────────────────
	// 云是画在太阳后面的;一片云正好压住日轮时,亮面缺一块会读成"弯月"。
	// 这里再叠 55% 的日核亮度,薄云挡住太阳时仍然是一枚发亮的日轮。
	if (sun_size > 0.0005) {
		float to_sun_after = dot(dir, normalize(sun_dir));
		float sun_ang_after = acos(clamp(to_sun_after, -1.0, 1.0));
		float core = 1.0 - smoothstep(sun_size * 0.85, sun_size,
				sun_ang_after);
		color += sun_color.rgb * core * sun_draw * 0.55;
	}

	COLOR = color;
}
