# shotpvp VPS 中转部署指南（阿里云轻量通用）

在 VPS 上跑权威服务端，玩家双方都连 VPS，绕开双方 NAT 打洞问题。

## 一、需准备

- 一台阿里云轻量服务器（Ubuntu/Debian/CentOS/Alibaba Cloud Linux 均可）
- 服务器公网 IP（控制台可见）
- 能通过 SSH 登录（root 或 sudo 用户）
- 本指南的安装命令会自动识别 Ubuntu（apt）与 CentOS/Aliyun（yum/dnf）

## 二、服务器端：一键部署

SSH 登录服务器后，粘贴执行（把 `YOUR_PUBLIC_IP` 换成你的公网 IP，仅用于提示文案）：

```bash
# 0. 识别系统（确认 apt 还是 yum/dnf）
cat /etc/os-release

# 1. 安装 Python 3 与 git（按系统自动选择包管理器）
if command -v apt >/dev/null 2>&1; then
  sudo apt update
  sudo apt install -y python3 git
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 git
else
  sudo yum install -y python3 git
fi

# 2. 拉取项目（只有 server.py 需要，但要带 protocol.py / game.py）
cd ~
git clone https://github.com/cong1ling/shotpvp.git
cd shotpvp
python3 --version   # 确认 >= 3.8
```

> 已装 git / python3 时会自然跳过，无副作用。

## 三、服务器端：启动服务端

### 方式 A：前台测试（先确认能通）

```bash
cd ~/shotpvp
python3 server.py
```

看到 `[server] 监听 0.0.0.0:47000` 即可。Ctrl+C 停止。

### 方式 B：systemd 后台常驻（推荐，重启自动拉起）

```bash
sudo tee /etc/systemd/system/shotpvp.service > /dev/null <<'EOF'
[Unit]
Description=ShotPVP authoritative server
After=network.target

[Service]
WorkingDirectory=/root/shotpvp
ExecStart=/usr/bin/python3 /root/shotpvp/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now shotpvp
sudo systemctl status shotpvp   # 确认 active (running)
```

> 如果 git clone 到你自己的用户目录而非 /root，把 WorkingDirectory 和 ExecStart 里的 `/root` 换成你的实际路径。

## 四、阿里云安全组：放行 UDP 47000

**这步很重要，默认全拦。**

1. 阿里云控制台 → 轻量应用服务器 → 你的实例 → **防火墙**（轻量叫"防火墙"，ECS 叫"安全组"）
2. **添加规则**：
   - 协议类型：**UDP**
   - 端口范围：**47000/47000**
   - 授权对象：**0.0.0.0/0**（或只放行你和朋友的公网 IP，更安全）
3. 保存

## 五、客户端连接（双方）

两侧都用**连接模式**直连 VPS，不再用 host/join-code：

```bash
python client.py --connect 你的VPS公网IP:47000
```

- 你（自己电脑）：`python client.py --connect 47.xxx.xxx.xxx:47000`
- 朋友电脑：同样 `python client.py --connect 47.xxx.xxx.xxx:47000`

两边都看到 `已连接，你的 player_id = 0/1` 后开始对战。

## 六、验证与排错

| 现象 | 检查 |
|---|---|
| 连不上、超时 | ① 服务器上 `sudo systemctl status shotpvp` 是否 running；② 阿里云防火墙是否放行 UDP 47000；③ `telnet IP 47000` 测 TCP 不通但 UDP 不能用 telnet——用 `nc -u -z IP 47000` 或在服务器 `ss -lunp \| grep 47000` |
| 服务器收不到包 | `sudo tcpdump -i any udp port 47000` 看有没有流量 |
| 延迟高 | 你到 VPS 的 RTT 决定体验；国内节点一般 30-60ms |

## 七、延迟预期

- 状态同步模型 + 客户端预测 + 100ms 插值缓冲，对 RTT 容忍度高
- 双方到 VPS 的 RTT 之和 = 你看到的对手延迟，国内节点可接受
- 若延迟 >150ms 再考虑后续优化（插值缓冲调整、可靠输入等）

## 八、安全提示

- 服务端无鉴权，任何人知道 IP:端口 都能加入（仅 2 人上限）。若要防陌生人，把防火墙授权对象限定为熟人的公网 IP
- systemd 服务开了自动重启，进程崩溃会自动拉起