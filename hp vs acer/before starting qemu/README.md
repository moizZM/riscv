# before starting qemu 


````py
sudo ip tuntap add dev tap0 mode tap user $USER
sudo ip link set tap0 up
```


````py
sudo iptables -t nat -A PREROUTING -p udp -d 192.168.1.25 --dport 12345 -j DNAT --to-destination 192.168.100.10:12345
sudo iptables -t nat -A POSTROUTING -p udp -d 192.168.100.10 --dport 12345 -j MASQUERADE
```



````py
sudo ip addr add 192.168.100.1/24 dev tap0
```


sudo qemu-system-riscv64 \
  -machine virt -nographic -m 2048 -smp 2 \
  -kernel /usr/lib/u-boot/qemu-riscv64_smode/uboot.elf \
  -append "console=ttyS0 root=/dev/vda1 rw" \
  -drive file=ubuntu-24.04.2-preinstalled-server-riscv64.img,if=virtio,format=raw \
  -netdev tap,id=net0,ifname=tap0,script=no,downscript=no \
  -device virtio-net-device,netdev=net0


sudo qemu-system-riscv64 \
  -nographic \
  -machine virt \
  -m 1G \
  -smp 2 \
  -kernel fw_jump.elf \
  -device virtio-net-device,netdev=net0 \
  -netdev tap,id=net0,ifname=tap0,script=no,downscript=no \
  -drive file=ubuntu-riscv.qcow2,format=qcow2



python3 hp-vm.py


sudo ip addr add 192.168.100.1/24 dev tap0



ip a show tap0

