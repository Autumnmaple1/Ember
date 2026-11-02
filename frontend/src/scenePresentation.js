export const SCENES = {
  neutral: { name: '位置待确认', image: null, safeArea: [0.25, 0.1, 0.75, 0.9] },
  library: { name: '图书馆', image: '/scenes/library.svg', safeArea: [0.25, 0.1, 0.75, 0.9] },
  dorm: { name: '宿舍', image: '/scenes/dorm.svg', safeArea: [0.25, 0.1, 0.75, 0.9] },
  campus: { name: '校园室外', image: '/scenes/campus.svg', safeArea: [0.25, 0.1, 0.75, 0.9] }
};

export function scenePresentation(config) {
  const key = config?.presentation?.background;
  const scene = SCENES[key] || SCENES.neutral;
  const period = ['day', 'dusk', 'night'].includes(config?.presentation?.period) ? config.presentation.period : 'day';
  return { ...scene, period, id: SCENES[key] ? key : 'neutral' };
}
