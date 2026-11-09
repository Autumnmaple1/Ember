import { useEffect, useRef, useState } from 'react';
import * as PIXI from 'pixi.js';
import { Live2DModel } from 'pixi-live2d-display';
import { LIVE2D_CONFIG } from './live2dConfig';
import { speechAnalyser } from './speechAudio';

// Expose PIXI to window for the plugin
window.PIXI = PIXI;
Live2DModel.registerTicker(PIXI.Ticker);

const Live2DViewer = ({ currentEmotion, audio, modelPath, lowPerformance = false, reduceMotion = false }) => {
    const containerRef = useRef(null);
    const canvasRef = useRef(null);
    const appRef = useRef(null);
    const modelRef = useRef(null);
    const [modelLoaded, setModelLoaded] = useState(false);
    const [modelError, setModelError] = useState(false);
    const [loadAttempt, setLoadAttempt] = useState(0);

    // 音频分析相关
    const lastValueRef = useRef(0);
    const lastTargetRef = useRef(0); // 预平滑音频输入
    const lastFormRef = useRef(0); // 嘴型(MouthForm)平滑
    const velocityRef = useRef(0); // 物理速度引用
    const closeDelayCounterRef = useRef(0); // 闭嘴延迟计数器

    // 口型同步辅助逻辑
    useEffect(() => {
        if (!audio || !modelLoaded || !modelRef.current || !LIVE2D_CONFIG.lipSync.enabled) return;

        // Playback owns the graph; model reloads and view changes must not mute it.
        const analyser = speechAnalyser(audio);
        if (!analyser) return;

        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        const { gain, minVolume, humanly, spring, phoneme } = LIVE2D_CONFIG.lipSync;

        // 每帧更新口型
        const updateMouth = () => {
            if (!modelRef.current?.internalModel?.coreModel) return;

            analyser.getByteFrequencyData(dataArray);

            let voiceSum = 0;
            const [start, end] = humanly.voiceRange;
            for (let i = start; i < end; i++) voiceSum += dataArray[i];
            const voiceAverage = voiceSum / (end - start);

            let normalized = Math.max(0, (voiceAverage - minVolume) / (255 - minVolume));
            let targetOpen = Math.pow(normalized, humanly.exponent) * gain;

            let lowSum = 0, lowCount = 0;
            let midSum = 0, midCount = 0;
            let highSum = 0, highCount = 0;

            const searchRange = Math.min(dataArray.length, 80);

            for (let i = 0; i < searchRange; i++) {
                if (i < phoneme.lowFreqBound) {
                    lowSum += dataArray[i];
                    lowCount++;
                } else if (i < phoneme.midFreqBound) {
                    midSum += dataArray[i];
                    midCount++;
                } else {
                    highSum += dataArray[i];
                    highCount++;
                }
            }

            const lowAvg = lowCount > 0 ? lowSum / lowCount : 0;
            const midAvg = midCount > 0 ? midSum / midCount : 0;
            const highAvg = highCount > 0 ? highSum / highCount : 0;
            const totalAvg = lowAvg + midAvg + highAvg;

            let targetForm = 0;
            if (totalAvg > 5 && targetOpen > 0.1) {
                const lowRatio = lowAvg / (totalAvg + 0.1);
                targetForm = (0.55 - lowRatio) * 5;
            }
            targetForm = Math.max(-1, Math.min(1.0, targetForm));

            if (targetForm < -0.3) {
                targetOpen *= 1.3;
            }
            targetOpen = Math.min(targetOpen, 1.0);

            if (targetOpen > 0.05) {
                closeDelayCounterRef.current = spring.closeDelayFrames;
            } else if (closeDelayCounterRef.current > 0) {
                closeDelayCounterRef.current--;
                targetOpen = Math.max(targetOpen, 0.1);
            }

            const smoothedTarget = lastTargetRef.current * (1 - spring.preSmoothing) + targetOpen * spring.preSmoothing;
            lastTargetRef.current = smoothedTarget;

            const smoothedForm = lastFormRef.current * (1 - phoneme.formSmoothing) + targetForm * phoneme.formSmoothing;
            lastFormRef.current = smoothedForm;

            const currentOpen = lastValueRef.current;
            const distance = smoothedTarget - currentOpen;
            const force = (distance * spring.stiffness) - (velocityRef.current * spring.damping);
            const acceleration = force / spring.mass;

            velocityRef.current += acceleration;
            let smoothedOpen = currentOpen + velocityRef.current;

            if (targetOpen === 0 && Math.abs(smoothedOpen) < 0.05 && Math.abs(velocityRef.current) < 0.05) {
                smoothedOpen = 0;
                velocityRef.current = 0;
            }
            smoothedOpen = Math.max(0, Math.min(1.0, smoothedOpen));

            lastValueRef.current = smoothedOpen;

            const coreModel = modelRef.current.internalModel.coreModel;
            coreModel.setParameterValueById('ParamMouthOpenY', smoothedOpen);
            coreModel.setParameterValueById('ParamMouthForm', smoothedForm);
        };

        const ticker = PIXI.Ticker.shared;
        ticker.add(updateMouth);

        return () => {
            ticker.remove(updateMouth);
            const coreModel = modelRef.current?.internalModel?.coreModel;
            if (coreModel) {
                coreModel.setParameterValueById('ParamMouthOpenY', 0);
                coreModel.setParameterValueById('ParamMouthForm', 0);
            }
            lastValueRef.current = 0;
            lastFormRef.current = 0;
        };
    }, [audio, modelLoaded]);

    useEffect(() => {
        if (!canvasRef.current) return;
        if (appRef.current) return;
        let resizeObserver;
        let disposed = false;
        const canvas = canvasRef.current;
        const contextLost = event => { event.preventDefault(); setModelLoaded(false); setModelError(true); };
        canvas.addEventListener('webglcontextlost', contextLost);

        const init = async () => {
            try {
                const app = new PIXI.Application({
                    view: canvasRef.current,
                    width: Math.max(1, containerRef.current.clientWidth),
                    height: Math.max(1, containerRef.current.clientHeight),
                    resolution: lowPerformance ? 1 : Math.min(window.devicePixelRatio || 1, 2),
                    autoDensity: true,
                    transparent: LIVE2D_CONFIG.pixi.transparent,
                    autoStart: LIVE2D_CONFIG.pixi.autoStart,
                });
                appRef.current = app;
                app.ticker.maxFPS = lowPerformance ? 30 : 60;

                const targetPath = modelPath || LIVE2D_CONFIG.model.path;
                const model = await Live2DModel.from(targetPath, {
                    autoInteract: false
                });

                if (disposed || !appRef.current || appRef.current !== app) {
                    model.destroy();
                    return;
                }

                modelRef.current = model;
                app.stage.addChild(model);

                model.anchor.set(LIVE2D_CONFIG.model.anchor.x, LIVE2D_CONFIG.model.anchor.y);
                const naturalWidth = model.width;
                const naturalHeight = model.height;
                const fit = () => {
                    if (disposed || !containerRef.current) return;
                    const width = Math.max(1, containerRef.current.clientWidth);
                    const height = Math.max(1, containerRef.current.clientHeight);
                    app.renderer.resize(width, height);
                    model.x = width / 2;
                    model.y = height / 2;
                    model.scale.set(Math.min(width / naturalWidth, height / naturalHeight) * LIVE2D_CONFIG.model.scale);
                };
                fit();
                resizeObserver = new ResizeObserver(fit);
                resizeObserver.observe(containerRef.current);

                console.log("Live2D Model Loaded");
                setModelLoaded(true);

            } catch (error) {
                console.error("Failed to load Live2D model:", error);
                if (!disposed) setModelError(true);
            }
        };

        init();

        return () => {
            disposed = true;
            canvas.removeEventListener('webglcontextlost', contextLost);
            resizeObserver?.disconnect();
            if (appRef.current) {
                appRef.current.destroy(false, { children: true });
                appRef.current = null;
                modelRef.current = null;
            }
        };
    }, [modelPath, loadAttempt, lowPerformance]);

    useEffect(() => {
        const media = window.matchMedia('(prefers-reduced-motion: reduce)');
        const update = () => {
            const model = modelRef.current;
            if (!model || !appRef.current) return;
            model.autoUpdate = !document.hidden && (!!audio || (!reduceMotion && !media.matches));
            if (document.hidden) appRef.current.stop(); else appRef.current.start();
        };
        document.addEventListener('visibilitychange', update);
        media.addEventListener('change', update);
        update();
        return () => { document.removeEventListener('visibilitychange', update); media.removeEventListener('change', update); };
    }, [modelLoaded, reduceMotion, audio]);


    useEffect(() => {
        if (modelLoaded && modelRef.current && ['normal', 'happy', 'sad', 'angry', 'questioning'].includes(currentEmotion)) {
            const model = modelRef.current;
            if (model.expression) {
                console.log(`Switching emotion to: ${currentEmotion}`);
                Promise.resolve(model.expression(currentEmotion)).catch(() => {});
            }
        }
    }, [currentEmotion, modelLoaded]);

    // 鼠标追踪 —— 头部、眼球、身体跟随鼠标
    useEffect(() => {
        if (!modelLoaded || !modelRef.current || !LIVE2D_CONFIG.mouseTracking?.enabled
            || reduceMotion || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

        const { smoothing, headAngleRange, bodyAngleRange, bodyFactor } = LIVE2D_CONFIG.mouseTracking;

        // 归一化鼠标坐标（-1 ~ 1，以屏幕中心为原点）
        const mousePos = { x: 0, y: 0 };
        // 当前平滑值
        const current = { angleX: 0, angleY: 0, eyeX: 0, eyeY: 0, bodyX: 0 };

        const onMouseMove = (e) => {
            const rect = containerRef.current?.getBoundingClientRect();
            if (!rect) return;
            mousePos.x = Math.max(-1, Math.min(1, (e.clientX - rect.left) / rect.width * 2 - 1));
            mousePos.y = Math.max(-1, Math.min(1, (e.clientY - rect.top) / rect.height * 2 - 1));
        };

        const surface = containerRef.current;
        surface.addEventListener('pointermove', onMouseMove);

        const updateTracking = () => {
            if (document.hidden) return;
            const coreModel = modelRef.current?.internalModel?.coreModel;
            if (!coreModel) return;

            // 目标值
            const targetAngleX = mousePos.x * headAngleRange;
            const targetAngleY = -mousePos.y * headAngleRange;  // Y 轴反转：鼠标上移 → 头部抬起
            const targetEyeX = mousePos.x;
            const targetEyeY = -mousePos.y;
            const targetBodyX = mousePos.x * bodyAngleRange * bodyFactor;

            // lerp 平滑插值
            current.angleX += (targetAngleX - current.angleX) * smoothing;
            current.angleY += (targetAngleY - current.angleY) * smoothing;
            current.eyeX += (targetEyeX - current.eyeX) * smoothing;
            current.eyeY += (targetEyeY - current.eyeY) * smoothing;
            current.bodyX += (targetBodyX - current.bodyX) * smoothing;

            // 设置参数
            coreModel.setParameterValueById('ParamAngleX', current.angleX);
            coreModel.setParameterValueById('ParamAngleY', current.angleY);
            coreModel.setParameterValueById('ParamEyeBallX', current.eyeX);
            coreModel.setParameterValueById('ParamEyeBallY', current.eyeY);
            coreModel.setParameterValueById('ParamBodyAngleX', current.bodyX);
        };

        const ticker = PIXI.Ticker.shared;
        ticker.add(updateTracking);

        return () => {
            surface.removeEventListener('pointermove', onMouseMove);
            ticker.remove(updateTracking);
        };
    }, [modelLoaded, reduceMotion]);

    return (
        <div ref={containerRef} className="live2d-viewer" aria-label="角色展示" data-renderer-state={modelError ? 'failed' : modelLoaded ? 'ready' : 'loading'}>
        <canvas
            id={LIVE2D_CONFIG.canvas.id}
            ref={canvasRef}
            aria-hidden="true"
        />
        {(!modelLoaded || modelError) && <div className="model-fallback" role="status"><span className="model-initial" aria-hidden="true">E</span>
            <p>{modelError ? '角色暂时无法显示，文字对话仍可使用。' : '正在准备角色…'}</p>
            {modelError && <button onClick={() => { setModelError(false); setModelLoaded(false); setLoadAttempt(value => value + 1); }}>重新加载角色</button>}
        </div>}
        </div>
    );
};

export default Live2DViewer;
