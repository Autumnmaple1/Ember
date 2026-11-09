import { useEffect, useRef, useState } from 'react';

const facts = scene => ({ character_location: scene.character_location || null, user_location: scene.user_location || null,
  communication_mode: scene.communication_mode || 'unknown', remote_contact_confirmed: scene.remote_contact_confirmed === true });

export default function SceneConfirmation({ scene, onSave, blockedReason = '' }) {
  const [character, setCharacter] = useState(scene.character_location || '');
  const [user, setUser] = useState(scene.user_location || '');
  const [mode, setMode] = useState(scene.communication_mode || 'unknown');
  const [contact, setContact] = useState(scene.remote_contact_confirmed === true);
  const [basis, setBasis] = useState(() => facts(scene));
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const feedback = useRef(null);
  useEffect(() => { if (error || saved) feedback.current?.scrollIntoView({ block: 'nearest' }); }, [error, saved]);
  const reload = () => {
    const latest = facts(scene);
    setBasis(latest); setCharacter(latest.character_location || ''); setUser(latest.user_location || '');
    setMode(latest.communication_mode); setContact(latest.remote_contact_confirmed); setError(''); setSaved(false);
  };
  const changed = JSON.stringify(facts(scene)) !== JSON.stringify(basis);
  return <form className="scene-confirmation" onSubmit={async event => {
    event.preventDefault();
    if (working) return;
    setSaved(false); setError('');
    if (blockedReason) { feedback.current?.scrollIntoView({ block: 'nearest' }); return; }
    const next = { character_location: character.trim() || null,
      user_location: mode === 'face_to_face' ? character.trim() || null : user.trim() || null,
      communication_mode: mode, remote_contact_confirmed: contact };
    if (mode === 'face_to_face' && !next.character_location) {
      setError('请填写双方的共同位置。'); return;
    }
    if (mode === 'remote' && !contact) { setError('请确认故事中的远程联系方式，或将交流方式设为未知。网页主动发言不受此限制。'); return; }
    setWorking(true);
    try { const result = await onSave(next, basis); setBasis(facts(result.state.scene)); setSaved(true); }
    catch (failure) { setError(failure.message || '场景保存失败，请重试'); }
    finally { setWorking(false); }
  }}><h3>确认场景</h3><p className="muted">保存后立即生效，不会发送聊天消息。日常对话仍可自然更新场景。</p>
    <label>交流方式<select aria-label="交流方式" disabled={working} value={mode} onChange={event => {
      const nextMode = event.target.value;
      if (nextMode === 'face_to_face' && !character.trim()) setCharacter(user);
      if (mode === 'face_to_face' && nextMode !== mode) setUser(character);
      setMode(nextMode); setSaved(false); setError('');
    }}><option value="unknown">未知</option><option value="face_to_face">面对面</option><option value="remote">远程</option></select></label>
    {mode === 'face_to_face' ? <>
      <label>共同位置（面对面）<input disabled={working} maxLength={150} value={character} onChange={event => { setCharacter(event.target.value); setSaved(false); setError(''); }} /></label>
      <p className="muted">保存后双方位置都会设为这里，不需要分别填写同一地点。</p>
    </> : <>
      <label>角色位置<input disabled={working} maxLength={150} value={character} onChange={event => { setCharacter(event.target.value); setSaved(false); setError(''); }} /></label>
      <label>你的位置<input disabled={working} maxLength={150} value={user} onChange={event => { setUser(event.target.value); setSaved(false); setError(''); }} /></label>
    </>}
    <label className="setting-row"><span>故事中已交换远程联系方式</span><input disabled={working} type="checkbox" checked={contact} onChange={event => { setContact(event.target.checked); setSaved(false); setError(''); }} /></label>
    <p className="muted">网页主动发言在设置中单独控制；这里的联系方式不代表已接通微信或电话。</p>
    <button type="submit" disabled={working} aria-describedby="scene-save-feedback">{working ? '正在保存…' : '保存场景'}</button>
    <div id="scene-save-feedback" ref={feedback}>
      {error && <p role="alert">{error}</p>}
      {saved && <p role="status">场景已保存。</p>}
      {!error && !working && blockedReason && <p role="status">{blockedReason}</p>}
      {changed && <p role="status">当前场景已有更新，保存前请重新核对。</p>}
    </div>
    {changed && <button type="button" disabled={working} onClick={reload}>载入最新场景并替换表单</button>}
  </form>;
}
