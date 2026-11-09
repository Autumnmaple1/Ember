import { useEffect, useState } from 'react';
import { API_BASE, getJSON } from '../api';
import { authSession, authenticationLost } from '../authSession';

export default function ServicePanel({ status }) {
  const [devices, setDevices] = useState([]);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  useEffect(() => { getJSON(`${API_BASE}/auth/devices`).then(data => setDevices(data.items)).catch(failure => setError(failure.message)); }, []);
  const revoke = async id => {
    setWorking(true); setError('');
    try {
      await getJSON(`${API_BASE}/auth/devices/${encodeURIComponent(id)}/revoke`, { method: 'POST' });
      if (id === authSession().session_id) authenticationLost();
      else setDevices(items => items.filter(item => item.id !== id));
    } catch (failure) { setError(failure.message); }
    finally { setWorking(false); }
  };
  return <section className="settings-section"><h3>连接与服务</h3>
    {error && <p role="alert">{error}</p>}
    <p>今天已使用：{Object.entries(status.limits).map(([key,limit]) => `${({ chat: '模型', embedding: '记忆向量', tts: '语音' })[key]} ${status.usage[key]?.requests || 0}/${limit}`).join(' · ')}</p>
    <p className="muted">额度按现实日期统计。请求失败也会保留记录，未完成回执不当作成功。</p>
    {status.error && <p role="status">{status.error}</p>}
    <p>最近自动备份：{status.jobs.find(item => item.status === 'completed') ? '已保存，可在存档中查看' : '尚未完成'}</p>
    {devices.map(item => <div className="setting-row" key={item.id}><span>{item.label}{item.id === authSession().session_id ? '（当前设备）' : ''}</span>
      <button disabled={working} onClick={() => revoke(item.id)}>{item.id === authSession().session_id ? '断开此设备' : '撤销连接'}</button></div>)}
  </section>;
}
