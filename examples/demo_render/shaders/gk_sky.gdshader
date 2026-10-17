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
// 太阳盘的大小(0.02 小亮点,0.2 大圆盘 —— 卡通风格喜欢大一点)
uniform float sun_size : hint_range(0.0, 0.5) = 0.06;

// ── 云层 ────────────────────────────────────────────────────────────
uniform float cloud_on : hint_range(0.0, 1.0) = 0.0;
uniform float cloud_amount : hint_range(0.0, 1.0) = 0.45;
uniform float cloud_scale = 3.0;
uniform float cloud_speed = 0.02;
// 云底压在地平线以上多少(太小云会糊到地平线上,太大就只剩头顶一片)
uniform float cloud_height : hint_range(0.0, 0.6) = 0.12;
uniform float cloud_softness : hint_range(0.01, 0.6) = 0.22;
uniform vec4 cloud_light : source_color = vec4(1.0, 0.98, 0.95, 1.0);
uniform vec4 cloud_dark : source_color = vec4(0.45, 0.48, 0.58, 1.0);
// 由 GDScript 累加(不是引擎的 TIME:`TIME` 在暂停时也走,而我们要能暂停)
uniform float time_offset = 0.0;

// ── 月亮(第一百四十二轮)───────────────────────────────────────────
// 由环境脚本按『环境_月亮』卡每帧写入:方向 / 颜色 / 盘大小 / 亮度 / 月相。
uniform vec3 moon_dir = vec3(0.0, -1.0, 0.0);
uniform vec4 moon_color : source_color = vec4(0.80, 0.86, 1.0, 1.0);
uniform float moon_size : hint_range(0.0, 0.5) = 0.035;
uniform float moon_energy : hint_range(0.0, 8.0) = 0.0;
// 月相:1 = 满月,0 = 新月(靠一条"晨昏线"把圆盘吃掉一块,不做精确几何)
uniform float moon_phase : hint_range(0.0, 1.0) = 1.0;

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

	// ── 太阳盘 ──────────────────────────────────────────────────
	// 边缘一圈柔和的溢出,免得小圆盘在低分辨率下闪
	float to_sun = dot(dir, normalize(sun_dir));
	float disc = smoothstep(1.0 - sun_size, 1.0 - sun_size * 0.55, to_sun);
	float halo = smoothstep(1.0 - sun_size * 6.0, 1.0, to_sun) * 0.35;
	color += sun_color.rgb * (disc + halo) * sun_energy
			* mix(0.15, 1.0, day_amount);

	// ── 月亮盘(和太阳盘同一条思路,再叠一个月相遮罩)──────────────
	if (moon_energy > 0.001) {
		vec3 mdir = normalize(moon_dir);
		float to_moon = dot(dir, mdir);
		float mdisc = smoothstep(1.0 - moon_size, 1.0 - moon_size * 0.55,
				to_moon);
		float mhalo = smoothstep(1.0 - moon_size * 5.0, 1.0, to_moon) * 0.25;
		// 月相:取一条垂直于月亮的"晨昏线",用 moon_phase 把它推到盘边
		vec3 side = normalize(cross(mdir, vec3(0.0, 1.0, 0.0)));
		float terminator = dot(dir, side) + (moon_phase * 2.0 - 1.0)
				* moon_size;
		float phase = smoothstep(0.0, 0.05, terminator);
		color += moon_color.rgb * (mdisc * mix(0.08, 1.0, phase) + mhalo)
				* moon_energy;
	}

	// ── 云层:把视线投到一个"云平面"上 ─────────────────────────
	// up 越小(越贴地平线)投影被拉得越长 —— 这正是真实云层的透视压缩。
	if (cloud_on > 0.5 && up > cloud_height) {
		vec2 plane = dir.xz / max(up, cloud_height) * cloud_scale;
		plane += vec2(time_offset * cloud_speed, time_offset * cloud_speed * 0.35);
		float density = gk_cloud_density(plane);
		float cover = smoothstep(1.0 - cloud_amount,
				1.0 - cloud_amount + cloud_softness, density);
		// 云被太阳照亮的那一侧偏亮(和太阳盘的同一个点乘)
		float lit = clamp(to_sun * 0.5 + 0.5, 0.0, 1.0);
		vec3 cloud_color = mix(cloud_dark.rgb, cloud_light.rgb, lit);
		cloud_color *= mix(0.22, 1.0, day_amount);
		// 靠近地平线的云薄一点(否则远处的云会糊成一条硬边)
		float fade = smoothstep(cloud_height, cloud_height + 0.25, up);
		color = mix(color, cloud_color, cover * cloud_on * fade);
	}

	COLOR = color;
}
