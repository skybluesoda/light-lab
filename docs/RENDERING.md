# 렌더링 모델

## 개념

래스터화는 삼각형을 화면에 투영해 픽셀의 표면 후보를 만듭니다. 레이 트레이싱은 광선과 장면의 교차를 찾는 기술입니다. 패스 트레이싱은 그 교차 연산으로 산란 경로를 샘플링하고 빛의 기여를 가중 평균하는 방식입니다. RT와 PT의 차이를 ‘광원 출발 대 카메라 출발’ 또는 ‘한 번 대 여러 번 반사’로 구분하지 않습니다.

GI는 표면 사이의 빛 전달까지 고려합니다. AO는 주변 방향이 막힌 정도로 주변광 음영을 근사하며, 색이 다른 표면에서 넘어오는 컬러 블리딩을 AO 자체로 계산하지 않습니다.

## 현재 Eevee

`scene_approx.py`가 직접광·그림자, 자체 ambient 필드, Sphere reflection probe를 결합합니다. Raytracing와 Fast GI는 꺼져 있고 라이트맵·Volume Probe 베이크를 하지 않습니다. 패널에는 Eevee 전용 Area Light 프록시가 있습니다.

`approx_lighting.py`의 각 광원 기여:

```text
d² = sum(((surface_position - light_position) * axis_scale)²)
f = 0.2 + 0.8 / (1 + d² / range²)
field_rgb += light_linear_rgb * intensity_gain * ambient_gain * f
added_rgb = material_base_color * field_rgb
```

이 값을 Eevee 재질의 Emission 성분으로 더합니다. Python 참조 함수와 같은 계산을 재질 노드가 수행하며, 렌더 중 픽셀마다 Python 함수를 호출하지 않습니다. 코브는 깊이 축 거리를 0.15배로 계산해 길쭉한 영향 범위를 만듭니다. 먼 곳에도 0.2 바닥값이 남습니다. gain은 예술적 보정값이며 물리적 단위 변환이 아닙니다.

필드는 실제 차폐·표면 방향·바운스·에너지 보존을 계산하지 않습니다. 벽을 추가해도 필드가 막히지 않고, 빨간 조형물의 색 전달도 자동 계산하지 않습니다. 광택 반사는 별도의 Sphere Probe 캡처와 Box Parallax로 근사하므로 시차·다중 반사에 오차가 있습니다.

## 현재 Cycles

원래 발광·Area Light·Principled 재질로 패스 트레이싱합니다. Eevee 전용 필드 출력과 패널 보조 광원을 제외합니다. 적응형 샘플 OFF, denoise ON, seed 42입니다. 바운스 값은 앱에서 노출하지 않고 코드에서도 별도로 고정하지 않습니다.

샘플 수는 렌더 표본 수이며 디노이징의 횟수가 아닙니다. 샘플을 늘려 추정 편차를 줄인 뒤 노이즈 제거를 적용합니다. Eevee의 누적 샘플과 Cycles의 경로 표본은 같은 연산량이 아닙니다.

## 비교의 한계

동일 장면·카메라·요청 광원 값을 공유하지만, 공급하는 조명 표현은 다릅니다. 현재 시스템은 엔진의 보편적 속도 순위를 증명하는 벤치마크가 아니라 선택한 조명 구성의 교육용 비교 도구입니다. 밝기가 비슷해도 물리적 정확성이 같다고 결론내리지 않습니다.

## 참고

- [PBRT: 광선 추적](https://pbr-book.org/4ed/Introduction/Photorealistic_Rendering_and_the_Ray-Tracing_Algorithm)
- [PBRT: 패스 트레이싱](https://pbr-book.org/4ed/Light_Transport_I_Surface_Reflection/Path_Tracing)
- [Blender: Sphere Probe](https://docs.blender.org/manual/en/4.5/render/eevee/light_probes/sphere.html)
- [Blender: Cycles](https://www.blender.org/features/rendering/)
