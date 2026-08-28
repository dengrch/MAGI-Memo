import { NodeBorderProgram } from '@sigma/node-border'
import { createNodeCompoundProgram, NodeCircleProgram } from 'sigma/rendering'
import type { NodeDisplayData } from 'sigma/types'
import { floatColor } from 'sigma/utils'

const MAX_AGENT_SEGMENTS = 6

const GLOW_VERTEX_SHADER = /* glsl */ `
attribute vec4 a_id;
attribute vec4 a_color;
attribute vec2 a_position;
attribute float a_size;
attribute float a_agentCount;
attribute vec4 a_agentColor0;
attribute vec4 a_agentColor1;
attribute vec4 a_agentColor2;
attribute vec4 a_agentColor3;
attribute vec4 a_agentColor4;
attribute vec4 a_agentColor5;
attribute float a_angle;

uniform mat3 u_matrix;
uniform float u_sizeRatio;
uniform float u_correctionRatio;

varying vec4 v_color;
varying vec2 v_diffVector;
varying float v_radius;
varying float v_agentCount;
varying vec4 v_agentColor0;
varying vec4 v_agentColor1;
varying vec4 v_agentColor2;
varying vec4 v_agentColor3;
varying vec4 v_agentColor4;
varying vec4 v_agentColor5;

const float bias = 255.0 / 254.0;

void main() {
  float size = a_size * u_correctionRatio / u_sizeRatio * 4.0;
  vec2 diffVector = size * vec2(cos(a_angle), sin(a_angle));
  vec2 position = a_position + diffVector;
  gl_Position = vec4((u_matrix * vec3(position, 1)).xy, 0, 1);

  v_diffVector = diffVector;
  v_radius = size / 2.0;
  v_agentCount = a_agentCount;
  v_agentColor0 = a_agentColor0;
  v_agentColor1 = a_agentColor1;
  v_agentColor2 = a_agentColor2;
  v_agentColor3 = a_agentColor3;
  v_agentColor4 = a_agentColor4;
  v_agentColor5 = a_agentColor5;

  #ifdef PICKING_MODE
  v_color = a_id;
  #else
  v_color = a_color;
  #endif
  v_color.a *= bias;
}
`

const GLOW_FRAGMENT_SHADER = /* glsl */ `
precision highp float;

varying vec4 v_color;
varying vec2 v_diffVector;
varying float v_radius;
varying float v_agentCount;
varying vec4 v_agentColor0;
varying vec4 v_agentColor1;
varying vec4 v_agentColor2;
varying vec4 v_agentColor3;
varying vec4 v_agentColor4;
varying vec4 v_agentColor5;

const vec4 transparent = vec4(0.0, 0.0, 0.0, 0.0);
const float PI = 3.141592653589793;
const float TAU = 6.283185307179586;

vec4 segmentColor(float segment) {
  if (segment < 0.5) return v_agentColor0;
  if (segment < 1.5) return v_agentColor1;
  if (segment < 2.5) return v_agentColor2;
  if (segment < 3.5) return v_agentColor3;
  if (segment < 4.5) return v_agentColor4;
  return v_agentColor5;
}

void main(void) {
  float distanceFromCenter = length(v_diffVector) / v_radius;

  #ifdef PICKING_MODE
  // The ordinary node program owns hit-testing. The larger glow must not
  // inflate the clickable area.
  gl_FragColor = transparent;
  #else
  float count = clamp(floor(v_agentCount + 0.5), 1.0, 6.0);
  float angle = mod(atan(v_diffVector.y, v_diffVector.x) + PI * 0.5 + TAU, TAU) / TAU;
  float segment = min(floor(angle * count), count - 1.0);
  float sectorPosition = fract(angle * count);
  float separator = count < 1.5
    ? 1.0
    : smoothstep(0.0, 0.045, min(sectorPosition, 1.0 - sectorPosition));
  float falloff = max(0.0, 1.0 - distanceFromCenter);
  float alpha = falloff * falloff * 1.35 * separator;
  vec4 agentColor = segmentColor(segment);
  // Sigma blends with ONE / ONE_MINUS_SRC_ALPHA, so RGB must be
  // premultiplied as well. Otherwise transparent pixels remain a solid disc.
  gl_FragColor = vec4(agentColor.rgb * alpha, agentColor.a * alpha);
  #endif
}
`

class ExplorationGlowProgram extends NodeCircleProgram {
  getDefinition() {
    return {
      ...super.getDefinition(),
      VERTEX_SHADER_SOURCE: GLOW_VERTEX_SHADER,
      ATTRIBUTES: [
        ...super.getDefinition().ATTRIBUTES,
        { name: 'a_agentCount', size: 1, type: WebGLRenderingContext.FLOAT },
        ...Array.from({ length: MAX_AGENT_SEGMENTS }, (_, index) => ({
          name: `a_agentColor${index}`,
          size: 4,
          type: WebGLRenderingContext.UNSIGNED_BYTE,
          normalized: true
        }))
      ],
      FRAGMENT_SHADER_SOURCE: GLOW_FRAGMENT_SHADER
    }
  }

  processVisibleItem(nodeIndex: number, startIndex: number, data: NodeDisplayData) {
    const explorerData = data as NodeDisplayData & { agentColors?: string[] }
    const colors = (explorerData.agentColors ?? ['#38bdf8']).slice(0, MAX_AGENT_SEGMENTS)
    const fallback = colors[0] ?? '#38bdf8'
    const array = this.array
    array[startIndex++] = data.x
    array[startIndex++] = data.y
    array[startIndex++] = data.size * 3.2
    array[startIndex++] = floatColor(fallback)
    array[startIndex++] = nodeIndex
    array[startIndex++] = Math.max(1, colors.length)
    for (let index = 0; index < MAX_AGENT_SEGMENTS; index += 1) {
      array[startIndex++] = floatColor(colors[index] ?? fallback)
    }
  }
}

export const ExplorationFrontierNodeProgram = createNodeCompoundProgram([
  ExplorationGlowProgram,
  NodeBorderProgram
])
