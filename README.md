# riscv

````bash

sudo qemu-system-riscv64 \
  -machine virt -nographic -m 2048 -smp 2 \
  -kernel /usr/lib/u-boot/qemu-riscv64_smode/uboot.elf \
  -append "console=ttyS0 root=/dev/vda1 rw" \
  -drive file=ubuntu-24.04.2-preinstalled-server-riscv64.img,if=virtio,format=raw \
  -netdev user,id=net0,hostfwd=udp::9000-:9000,hostfwd=udp::9001-:9001 \
  -device virtio-net-device,netdev=net0


````
