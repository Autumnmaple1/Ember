export const LIVE2D_CONFIG = {
    model: {
        path: '/models/LSS/LSS.model3.json',
        scale: 1.0,
        anchor: { x: 0.5, y: 0.5 }
    },
    canvas: {
        width: 1200,
        height: 1800,
        id: 'live2d-canvas',
        style: {
            position: 'fixed',
            bottom: 0,
            right: 0,
            zIndex: 1,
            pointerEvents: 'none'
        }
    },
    pixi: {
        transparent: true,
        autoStart: true
    },
    lipSync: {
        enabled: true,
        gain: 2.5,
        minVolume: 35,
        // 弹簧阻尼算法配置 (Spring-Damper Model)
        spring: {
            stiffness: 0.25,     // 增加弹性，使动作更敏捷
            damping: 0.3,        // 降低阻尼
            mass: 0.8,           // 减小质量，增加灵活性
            preSmoothing: 0.15,  // 略微降低预平滑
            closeDelayFrames: 2  // 大幅减少闭嘴延迟，显著改善"一直张口"的问题
        },
        // 音素模拟配置
        phoneme: {
            lowFreqBound: 10,
            midFreqBound: 25,
            formSmoothing: 0.15
        },
        // 真人感模拟配置
        humanly: {
            attack: 0.5,
            decay: 0.2,
            exponent: 2.0,     // 增加指数，使口型对音量变化更敏感（小声不张嘴，大声才张大）
            voiceRange: [2, 60] // 略微上移起始频率，过滤掉可能的低频直流偏置或底噪
        }
    },
    // 鼠标追踪配置 —— 模型头部、眼球、身体跟随鼠标
    mouseTracking: {
        enabled: true,
        smoothing: 0.15,       // lerp 系数，越小越平滑（0~1）
        headAngleRange: 30,    // 头部旋转最大角度
        bodyAngleRange: 10,    // 身体旋转最大角度
        bodyFactor: 0.4,       // 身体跟随程度（相对头部）
    }
};
