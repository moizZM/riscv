# riscv

````bash

sudo qemu-system-x86_64 \
  -machine accel=kvm:tcg \
  -m 4G -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,readonly=off,file=/usr/share/OVMF/OVMF_VARS_4M.fd \
  -drive file=/home/moiz-malik/Downloads/ubuntu24.qcow2,format=qcow2,if=virtio \
  -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=udp::9000-:9000,hostfwd=udp::9001-:9001 \
  -device e1000,netdev=net0 \
  -display gtk

````
