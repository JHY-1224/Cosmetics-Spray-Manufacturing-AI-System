"""Only terminate the process matching this launcher's receipt and start time."""
import signal
from status_assistant import owned_process, settings_from_args

if __name__ == '__main__':
    process = owned_process(settings_from_args())
    if not process:
        raise SystemExit('没有核验到本助手自有进程；未停止任何进程')
    process.send_signal(signal.SIGTERM)
    print('已向本助手发送正常停止信号；未操作其他服务或模型进程')
