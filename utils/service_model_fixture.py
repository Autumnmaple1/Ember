"""Fixed synthetic model protocol fixture, isolated container network only."""
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI()


@app.get('/health')
def health():
    return {'synthetic': True}


@app.post('/v1/embeddings')
def embedding(body: dict):
    return {'object': 'list', 'model': body.get('model'), 'data': [
        {'object': 'embedding', 'index': 0, 'embedding': [1.0] + [0.0] * 1535}],
        'usage': {'prompt_tokens': 10, 'total_tokens': 10}}


@app.post('/v1/chat/completions')
def chat(body: dict):
    usage = {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}
    if 'small' in body.get('model', ''):
        text = str(body.get('messages', [{}])[-1].get('content', ''))
        previous = {}
        for marker in ('当前状态：', '上次状态仅为历史背景：'):
            if marker in text:
                try: previous = json.JSONDecoder().raw_decode(text.split(marker, 1)[1].lstrip())[0]
                except ValueError: pass
        scene = previous.get('scene') or {'character_location': '测试图书馆', 'user_location': None,
            'communication_mode': 'unknown', 'remote_contact_confirmed': False}
        content = json.dumps({'P': 5, 'A': 4, 'D': 5, '客观情境': (scene.get('character_location') or '测试场景') + '；合成部署验证',
            '近期综合轨迹': '合成测试', '内心活动': '', '近期目标': '仅执行部署验证', 'scene': scene,
            'memory_encode': False, 'should_speak': False, 'is_sleeping': False}, ensure_ascii=False)
    else:
        content = '这是部署验证的合成回复，消息与活动会保留。'
    if not body.get('stream'):
        return {'id': 'fixture', 'object': 'chat.completion', 'model': body.get('model'),
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}], 'usage': usage}
    async def events():
        for i in range(0, len(content), 4):
            yield 'data: ' + json.dumps({'id': 'fixture', 'object': 'chat.completion.chunk', 'model': body.get('model'),
                'choices': [{'index': 0, 'delta': {'content': content[i:i+4]}, 'finish_reason': None}]}, ensure_ascii=False) + '\n\n'
        yield 'data: ' + json.dumps({'id': 'fixture', 'choices': [], 'usage': usage}) + '\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(events(), media_type='text/event-stream')


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=9000, log_level='warning')
