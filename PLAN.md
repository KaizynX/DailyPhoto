# DailyPhoto 方案与调研记录

更新日期：2026-08-01

## 目标

在固定工位的 Windows 电脑上，以尽可能低的日常操作成本，记录实际来到工位的每一天。

确定的产品行为：

- 不按固定钟点拍摄，而是在每天第一次登录或解锁后启动。
- 当天只保留一次“已完成”状态；照片成功保存才算完成。
- 拍摄前显示摄像头画面并倒计时 10 秒。
- 拍摄后显示静态预览，允许保存或重拍。
- 预览确认界面 15 秒没有操作时默认保存，避免无人确认导致当天遗漏。
- 通过 Windows 任务计划程序持久注册，重启、睡眠和休眠后仍有效。
- 当前阶段只保存本地；不会自动上传。

## 为什么选择自制轻量工具

调研过的现成方案各自只覆盖一部分需求：

- Windows 相机具有拍照、倒计时和查看最近照片等能力，但没有“每日首次解锁、当天去重、确认超时自动保存”这一完整工作流。
  - <https://support.microsoft.com/en-us/windows/how-to-use-the-windows-camera-app-ea40b69f-be6a-840e-9c8c-1fd6eea97c22>
- Yawcam 是 Windows 摄像头软件，主要面向定时拍摄、监控和流媒体，并不针对每日自拍确认流程。
  - <https://www.yawcam.com/>
- Journal2Day 能从电脑摄像头记录每日自拍，但它本质上是需要进入的日记软件，不能替代解锁触发。
  - <https://www.kabsoftware.com/journal2day/>
- AgeLapse 可以对长期积累的人脸照片进行特征点检测、对齐并生成稳定的成长延时视频，但它负责后期处理，不负责本方案的采集触发。
  - <https://github.com/hugocornellier/agelapse>

因此，本项目只实现缺失的“可靠触发 + 当日去重 + 轻量确认”部分，并使用普通 JPEG 作为开放的数据格式，避免将照片锁在某个应用数据库里。

## 运行设计

### 触发

Windows 任务计划程序包含两个触发器：

- 当前用户登录；
- 当前用户会话解锁。

登录触发覆盖开机后的首次进入，解锁触发覆盖睡眠、休眠和锁屏恢复。两个触发器可能相邻发生，因此程序还使用单实例互斥和当天照片检查来防止重复窗口。

### 当天完成判定

照片保存在：

```text
photos/YYYY/MM/YYYY-MM-DD_HH-mm-ss.jpg
```

当日目录中存在符合日期前缀的 JPEG，即认为当天已经完成。只有文件成功写入后才成立。拍摄和确认过程中不会提前写入“完成”标志。

通过 `--force` 启动时忽略当天完成检查，用于测试；测试保存的照片仍然是正常照片。

### 拍摄交互

1. 打开摄像头并显示镜像实时预览。
2. 画面稳定期间进行 10 秒倒计时。
3. 从最新画面拍照，显示静态预览。
4. 用户可保存或重拍。
5. 15 秒无选择则自动保存。

如果摄像头正被会议软件独占，程序会显示错误而不会把当天标记为完成；下一次解锁仍可再次尝试。

### 本地性和隐私

- 摄像头帧只在本机内存中处理。
- 程序没有网络上传代码。
- 摄像头指示灯会在预览和拍摄时亮起。
- 文件夹中的照片是普通 JPEG，可随时复制、查看或迁移。

## 配置

`config.json` 中的主要选项：

- `capture_delay_seconds`：拍摄前倒计时，默认 10。
- `confirmation_timeout_seconds`：拍摄后默认保存超时，默认 15。
- `camera_index`：摄像头编号，默认 0。
- `mirror_image`：预览和保存是否镜像，默认 true。
- `jpeg_quality`：JPEG 质量，默认 95。

## 云备份：未来扩展

当前版本只存本地。未来建议按以下优先级扩展：

1. 将 `photos` 文件夹同步到 OneDrive 等常规云盘。
2. 定期复制到移动硬盘，形成与同步服务独立的恢复副本。
3. GitHub 私有仓库只作为可选的额外副本，不作为主要照片云。

GitHub 对普通 Git 对象强制限制为 100 MB，并建议控制仓库总大小；大量二进制照片会让完整克隆逐年变慢。Git LFS 更适合二进制，但个人免费计划的存储和下载配额有限，恢复体验也不如网盘。

- GitHub 仓库限制：<https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits>
- Git LFS 说明：<https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage>
- Git LFS 计费和免费配额：<https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-git-large-file-storage/upgrading-git-large-file-storage>

如果以后加入云备份，应默认关闭，并明确显示目标服务、最近成功时间及失败状态。

## AgeLapse：未来可选计划

长期积累后可使用 AgeLapse：

- 检测眼睛等人脸特征点；
- 对照片做缩放、旋转和平移对齐；
- 生成稳定的 photo-a-day 成长延时视频；
- 支持 Windows 桌面。

本项目会保留原始 JPEG，不在采集阶段裁剪或覆盖，以便未来用 AgeLapse 或其他工具重新处理。可以另行生成 `aligned` 和 `videos` 目录，任何派生文件都不替换原图。

## 后续候选功能

- 云盘备份及失败提醒；
- 月度日历视图和照片浏览；
- 调用 AgeLapse 生成对齐照片；
- 每月或每年自动生成延时视频；
- 多摄像头选择界面；
- 拍摄成功后的 Windows 通知；
- 照片完整性校验和独立离线备份提醒。
