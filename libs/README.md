# JDBC 驱动

本目录用于存放数据库 JDBC 驱动 JAR 文件。

## 需要放入的驱动

| 数据库 | JAR 文件名 | 说明 |
|--------|-----------|------|
| 达梦(DM) | `DmJdbcDriver18.jar` | 达梦 JDBC 驱动，从 [达梦官网](https://www.dameng.com) 或 Maven 中央仓库下载 |
| Oracle | `ojdbc8.jar` | Oracle JDBC 驱动，从 [Oracle 官网](https://www.oracle.com/database/technologies/appdev/jdbc-downloads.html) 下载 |

## 放置方式

将下载的 JAR 文件直接放入本目录即可，打包脚本会自动扫描 `libs/*.jar` 并打入可执行文件。

## 验证

```bash
# 达梦驱动
java -jar libs/DmJdbcDriver18.jar -h

# Oracle 驱动（验证版本）
unzip -p libs/ojdbc8.jar META-INF/MANIFEST.MF
```
