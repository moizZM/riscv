````bash

nano ~/riscv-vm/start-qemu.sh

````

````bash

#!/bin/bash

# Set paths to your image and kernel
IMAGE="ubuntu-24.04.2-preinstalled-server-riscv64.img"
KERNEL="/usr/lib/u-boot/qemu-riscv64_smode/uboot.elf"

# QEMU Command
sudo qemu-system-riscv64 \
  -machine virt -nographic -m 2048 -smp 2 \
  -kernel "$KERNEL" \
  -append "console=ttyS0 root=/dev/vda1 rw" \
  -drive file="$IMAGE",if=virtio,format=raw \
  -netdev tap,id=net0,ifname=tap0,script=no,downscript=no \
  -device virtio-net-device,netdev=net0

````


````bash

chmod +x ~/riscv-vm/start-qemu.sh

```` 

````bash

cd ~/riscv-vm
./start-qemu.sh

```` 
