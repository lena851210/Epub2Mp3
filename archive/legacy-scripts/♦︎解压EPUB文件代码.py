import zipfile
import os
import tkinter as tk
from tkinter import filedialog, messagebox

def extract_epub():
    # 创建文件选择对话框
    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口
    
    # 选择 EPUB 文件
    epub_path = filedialog.askopenfilename(
        title="选择 EPUB 文件",
        filetypes=[("EPUB 文件", "*.epub"), ("所有文件", "*.*")]
    )
    
    if not epub_path:
        messagebox.showinfo("信息", "未选择文件")
        return
    
    # 生成默认解压目录名
    epub_name = os.path.splitext(os.path.basename(epub_path))[0]
    default_extract_dir = os.path.join(
        os.path.dirname(epub_path), 
        f"{epub_name}_解压"
    )
    
    # 询问是否使用默认解压目录
    use_default = messagebox.askyesno(
        "解压目录", 
        f"是否解压到默认目录?\n{default_extract_dir}\n\n选择'否'可以自定义目录"
    )
    
    if use_default:
        extract_dir = default_extract_dir
    else:
        # 选择自定义解压目录
        extract_dir = filedialog.askdirectory(
            title="选择解压目录",
            initialdir=os.path.dirname(epub_path)
        )
        if not extract_dir:
            messagebox.showinfo("信息", "未选择解压目录")
            return
    
    # 检查目录是否已存在
    if os.path.exists(extract_dir):
        overwrite = messagebox.askyesno(
            "目录已存在", 
            f"目录 '{extract_dir}' 已存在。是否覆盖?"
        )
        if not overwrite:
            messagebox.showinfo("信息", "操作已取消")
            return
    
    try:
        # 确保目录存在
        os.makedirs(extract_dir, exist_ok=True)
        
        # 解压 EPUB 文件
        with zipfile.ZipFile(epub_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        messagebox.showinfo("成功", f"EPUB 文件已成功解压到:\n{extract_dir}")
        
        # 询问是否打开解压目录
        open_folder = messagebox.askyesno("打开目录", "是否打开解压后的文件夹?")
        if open_folder:
            os.startfile(extract_dir)  # Windows
            # 对于 macOS: os.system(f'open "{extract_dir}"')
            # 对于 Linux: os.system(f'xdg-open "{extract_dir}"')
    
    except zipfile.BadZipFile:
        messagebox.showerror("错误", "选择的文件不是有效的 EPUB/ZIP 文件")
    except Exception as e:
        messagebox.showerror("错误", f"解压过程中出现错误:\n{str(e)}")

if __name__ == "__main__":
    extract_epub()