import { useState } from 'react';

export default function SceneConfirmation({ scene, onEdit }) {
  const [character, setCharacter] = useState(scene.character_location || '');
  const [user, setUser] = useState(scene.user_location || '');
  const [mode, setMode] = useState(scene.communication_mode || 'unknown');
  const [contact, setContact] = useState(scene.remote_contact_confirmed === true);
  return <form className="scene-confirmation" onSubmit={event => {
    event.preventDefault();
    onEdit(`我想确认当前场景：你现在在${character.trim() || '未知位置'}，我在${user.trim() || '未知位置'}。交流方式${({ face_to_face: '是面对面', remote: '是远程', unknown: '尚不确定' })[mode]}。${contact ? '我们已经确认交换过微信或电话联系方式。' : '远程联系方式尚未确认。'}请核对后再更新。`);
  }}><h3>确认场景</h3><p className="muted">填写后放入草稿，由你发送。后台校验后才会更新事实；不会因切换界面改变双方位置。</p>
    <label>角色位置<input maxLength={150} value={character} onChange={event => setCharacter(event.target.value)} /></label>
    <label>你的位置<input maxLength={150} value={user} onChange={event => setUser(event.target.value)} /></label>
    <label>交流方式<select value={mode} onChange={event => setMode(event.target.value)}><option value="unknown">未知</option><option value="face_to_face">面对面</option><option value="remote">远程</option></select></label>
    <label className="setting-row"><span>确实交换过远程联系方式</span><input type="checkbox" checked={contact} onChange={event => setContact(event.target.checked)} /></label>
    <button>填写场景确认草稿</button>
  </form>;
}
