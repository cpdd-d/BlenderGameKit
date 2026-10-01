shader_type spatial;
// 故意写错的着色器:用于验证 scripts/verify_shaders.py 真的能抓到错误。
// 全部是真实会犯的错:
//   1. 类型不匹配
//   2. 用了不存在的内置量
//   3. 函数名写错
//   4. 括号不匹配

uniform vec4 颜色 : source_color = vec4(1.0);

void fragment() {
	vec3 a = 1.0;                     // 错误 1:float 赋给 vec3
	ALBEDO = nonexistent_builtin;     // 错误 2:不存在的内置量
	ALBEDO = texture(no_such_sampler, UV).rgb;   // 错误 3:未声明的 sampler
	COLOR = vec3(1.0;                 // 错误 4:括号没闭合
}
